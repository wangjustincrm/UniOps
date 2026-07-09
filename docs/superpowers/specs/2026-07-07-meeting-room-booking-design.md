# Meeting Room Booking Module — Design Spec

- Date: 2026-07-07
- Status: Approved (user confirmed design 2026-07-07)
- Source PRD: `MeetingRoomAppointPRD.md` (repo root)
- Scope: V1 = PRD Phase-1 MVP + basic recurring meetings

## 1. 背景与目标

企业内部会议室预订系统,接入 UniOps 平台。核心价值:会议室资源可见、冲突预防、预订后自动邮件通知 + Outlook Classic Desktop 日历邀请(iMIP over SMTP,无 Exchange)。

成功指标(来自 PRD):预订成功率 ≥95%、冲突率 ≤1%、单次预订 ≤60 秒、邮件/邀请生成成功率 ≥99%。

## 2. 总体架构

照 VMS 模式的独立模块:

| 组件 | 说明 |
|---|---|
| booking-api | FastAPI,dev 端口 **8010**(8009=identity 之后下一个);独立 alembic 迁移链(独立 version 表,参照 budget-api 的做法);共享 Postgres;identity JWT 鉴权 |
| booking 前端 | React/Vite,dev 端口 **5178**(5177=finance 之后);生产子域 `booking.<域名>`;自有 AppLayout + 侧栏;`useBranding('booking')` 品牌区;UI 文案全英文 |
| 镜像表 | `user_mirror`(参会人通讯录来源 = identity users 表)。建镜像前逐列核对 `information_schema`,不套 TimestampMixin 惯例 |

### Portal 接入(照模块接入规范)

- `portal/src/components/layout/navConfig.tsx` MODULES 加 booking 项,`anyPermission: ['view_booking', 'manage_meeting_rooms']`
- `portal/src/pages/PortalHome.tsx` MODULES 数组按 matrix+role 条件加卡片;链接指向模块根 `${BOOKING_URL}/#__session=<base64>`,不深链
- Portal AdminPanel `MODULE_TAGLINE_KEYS` 加 `{ key: 'booking', label: 'Booking' }`
- 新 origin(dev `http://localhost:5178` + 生产子域)加入**全部后端** `ALLOWED_ORIGINS`:8 个 `*/app/core/config.py` 默认值 + epms/vms-api compose env 覆盖 + `.env.prod`
- booking 前端 AppLayout 根 `/` 按权限选落地页:等 matrix 加载 → 第一个有权限的 nav 项 → `navigate(homePath, {replace})`;无任何权限显示"无权限 + 返回 Portal"

### 权限模型(EPMS Access Control Matrix 新键)

| 权限键 | 角色语义 | 能力 |
|---|---|---|
| `view_booking` | 普通员工(默认全角色开) | 查询会议室、发起预订、修改/取消**自己**的预订、查看自己的会议 |
| `manage_meeting_rooms` | 行政/会议室管理员 | 会议室 CRUD、批量导入、查看/管理全部预订、强制取消、规则与 SMTP 配置、通知/同步监控 |

PRD 的"IT/系统管理员"复用现有 `system_admin`(matrix 全通过)。后端每个接口按同样两键鉴权,不硬编码 role。

## 3. 数据模型(booking-api 自有表)

会议室仅本模块使用,不进 mdm-api(非跨系统主数据)。

### meeting_rooms

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| name | text NOT NULL | |
| code | text NOT NULL UNIQUE | 组织内唯一 |
| campus / building / floor / area | text | 园区/楼栋/楼层/区域,筛选维度 |
| capacity | int NOT NULL CHECK (capacity > 0) | 容纳人数 |
| equipment | JSONB(string 数组) | `tv` / `projector` / `whiteboard` / `video_conf` / `phone_conf` |
| room_type | text | `standard` / `training` / `boardroom` / `multi_function` |
| open_time_start / open_time_end | time | 可预订开放时段,默认 08:00–20:00 |
| advance_booking_days | int | 默认 30 |
| status | text | `available` / `disabled` / `maintenance` |
| owner_department | text | 负责人或管理部门 |
| notes | text | 备注(维修原因等,员工端可见) |
| image_file_ids | JSONB(UUID 数组) | 图片走 file-api |
| created_at / updated_at | timestamptz | |

规则:disabled/maintenance 不可预订、不参与推荐;修改基础信息不影响历史预订。

### bookings

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID PK | |
| room_id | UUID FK → meeting_rooms | |
| title / description | text | 会议主题/说明 |
| organizer_id | UUID | 发起人(users) |
| attendee_ids | JSONB(UUID 数组) | 参会人,来源企业通讯录(identity users) |
| starts_at / ends_at | timestamptz NOT NULL | ends_at > starts_at |
| status | text | `confirmed` / `cancelled` |
| series_id | UUID NULL | 周期会议系列 ID;单次会议为 NULL |
| rrule | text NULL | 系列的 RRULE 字符串,系列内每行冗余存同值 |
| calendar_uid | text | iCalendar UID;**系列共用一个 UID**,单次会议每条一个 |
| ical_sequence | int DEFAULT 0 | 每次修改 +1 |
| sync_status | text | `pending` / `sent` / `failed` / `compensating` |
| created_at / updated_at | timestamptz | |

**排他约束(冲突硬保证)**:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE bookings ADD CONSTRAINT no_double_booking
  EXCLUDE USING gist (room_id WITH =, tstzrange(starts_at, ends_at) WITH &&)
  WHERE (status = 'confirmed');
```

应用层提交前另做预检(友好报错 + 推荐);约束是并发兜底,违反时捕获 IntegrityError 返回同样的冲突响应。

### notification_log

| 字段 | 说明 |
|---|---|
| id / booking_id | |
| notif_type | `created` / `updated` / `cancelled` / `sync_alert` |
| recipients | JSONB(email 数组) |
| status | `pending` / `sent` / `failed` |
| error | 失败原因 |
| retry_count | 已重试次数 |
| sent_at / created_at | |

### booking_config(单行表,照 vms_config 模式)

- `smtp_settings` JSONB:host/port/user/password/use_tls/from_email;为空回退共享 `company_config` SMTP(照抄 vms-api `_load_smtp_config` 双级 lookup)
- `rules` JSONB:slot_minutes=15、min_duration=15、max_duration=240、advance_days=30、default_open_start/end、notify_room_admin(bool)
- `organizer_mode`:`system`(默认,ORGANIZER=系统邮箱)/ `initiator`(ORGANIZER=发起人,需网关允许代发)

### booking_audit_log

id、booking_id、action(`create`/`update`/`cancel`/`force_cancel`)、actor_id、before/after JSONB 快照、created_at。

## 4. 核心业务逻辑

### 4.1 预订创建(自动通过)

1. 校验:时间合法(end>start、时长 15min–4h、15 分钟粒度对齐)、在开放时段与提前预订窗口内、会议室 available、`view_booking` 权限
2. 预检冲突:查该 room 在 `[starts_at, ends_at)` 有无 confirmed 重叠
3. 无冲突 → INSERT(排他约束兜底并发);有冲突 → 400 + 推荐 payload,不落库
4. 落库后:写 audit log → 入队通知(邮件 + iCal 邀请)→ 返回成功(邮件失败不影响预订成功,PRD 11.2)

### 4.2 周期性会议

- 表单支持固定规则:频率(daily/weekly)、间隔、按 N 次或截止日期结束(上限:不超过 advance_days 窗口)
- 提交时按 RRULE 展开为 N 条独立 bookings(共享 series_id、calendar_uid、rrule);**任一 occurrence 冲突则整个系列创建失败**并提示冲突的日期(PRD:不可与已存在预订冲突)
- V1 周期系列只支持**整系列取消**(全部未开始 occurrence 置 cancelled + 发一封 METHOD:CANCEL);逐次修改/例外日期放二期。修改系列 = UI 引导"取消整系列后重新创建"(PATCH 单个系列 occurrence 返回 400 并提示);单次会议(series_id 为 NULL)正常修改
- 日历侧:发**一个带 RRULE 的周期 VEVENT**(单封邀请,Outlook 生成周期事项),DB 侧 N 条占用记录

### 4.3 修改与取消

- 发起人只能改自己的;管理员(manage_meeting_rooms)可改/强制取消全部
- 已开始的会议默认不允许改时间,管理员可特殊处理
- 修改:释放原时段(同事务内更新)、复用 calendar_uid、ical_sequence+1、发 METHOD:REQUEST 更新邀请 + 更新邮件
- 取消:status=cancelled(时段立即释放,排他约束 WHERE 子句自动放行)、发 METHOD:CANCEL + 取消邮件
- 管理员停用会议室时:列出受影响的未来预订,由管理员选择通知发起人改期或批量取消(取消走正常取消流程含通知)

### 4.4 可用性查询与推荐(PRD 9.4)

- 查询:给定时间段 + 筛选条件(园区/楼栋/楼层/区域/容量/设备/类型),返回空闲会议室列表
- 目标会议室冲突时,返回:
  1. 该会议室最近的相邻空闲时段(15 分钟粒度,前后各找)
  2. 相同时间段的替代会议室,排序:同楼层同容量区间 → 相邻楼层/同区域 → 其余
  3. 无完全匹配时降级:先满足时间 → 再容量 → 再设备
- 提交前前端实时调用冲突预检接口展示提示

### 4.5 状态计算(读时计算,无定时任务)

| 状态 | 判定 |
|---|---|
| disabled / maintenance | room.status 直接映射(维修展示 notes 原因) |
| in_use(使用中) | 当前时间落在某 confirmed booking 内 |
| starting_soon(即将开始) | 距下一场开始 ≤15 分钟 |
| booked(已预订) | 今天还有未开始的 confirmed booking |
| free(空闲) | 其余 |

前端列表页 60 秒轮询刷新(满足"刷新延迟≤1 分钟")。详情页含当天时间轴(open hours 内按 15 分钟格)、未来 7 天占用、下一场会议时间。

## 5. 邮件与 Outlook 邀请(iMIP over SMTP)

### 5.1 邀请生成

- 依赖 `icalendar` 库;VEVENT 含 UID(稳定)、SEQUENCE、DTSTART/DTEND(带 TZID)、SUMMARY、DESCRIPTION、LOCATION(会议室名+位置)、ORGANIZER、ATTENDEE 列表;周期系列附 RRULE
- 创建/修改:`METHOD:REQUEST`;取消:`METHOD:CANCEL`(同 UID,SEQUENCE 递增)
- 邮件结构:`multipart/alternative`(text/plain 可读正文 + `text/calendar; method=REQUEST; charset=utf-8`)+ `.ics` 附件(`application/ics`),兼容 Outlook Classic Desktop
- ORGANIZER 按 `organizer_mode` 配置:默认系统邮箱(From 与 ORGANIZER 一致,避免网关拒代发);V1 不解析参会人接受/拒绝回复

### 5.2 收件人

发起人 + 全部参会人 + 会议室管理员(booking_config 开关)。同一事件只发一次正式通知,失败走重试补发。

### 5.3 失败补偿

- 预订先落库;发送失败 → notification_log status=failed + booking.sync_status=failed
- 后台 asyncio 循环(照 vms-api scheduled_jobs 模式)定期重试 failed 记录,指数退避,上限 N 次
- 重试耗尽 → 给管理员发 sync_alert 邮件 + 管理后台展示同步异常
- 管理后台支持手动重发;补偿成功回写 sync_status=sent
- SMTP 未配置时 log-only 优雅降级(照 VMS "smoke-test mode")

## 6. API 概览(/api/v1)

| 端点 | 权限 | 说明 |
|---|---|---|
| GET /rooms | view_booking | 列表+筛选+计算状态 |
| GET /rooms/{id} | view_booking | 详情+当天时间轴+7 天占用 |
| GET /rooms/availability | view_booking | 按时段/条件查空闲会议室 |
| POST /bookings/precheck | view_booking | 冲突预检+推荐(不落库) |
| POST /bookings | view_booking | 创建(含周期系列) |
| GET /bookings/mine | view_booking | 我的预订 |
| PATCH /bookings/{id} | 本人或 admin | 修改 |
| POST /bookings/{id}/cancel | 本人或 admin | 取消(?series=true 整系列) |
| GET /admin/bookings | manage_meeting_rooms | 全部预订+导出 CSV |
| POST/PATCH/DELETE /admin/rooms | manage_meeting_rooms | 会议室 CRUD |
| POST /admin/rooms/import | manage_meeting_rooms | xlsx 批量导入 |
| GET/PUT /admin/config | manage_meeting_rooms | 规则+SMTP 配置 |
| GET /admin/notifications | manage_meeting_rooms | 通知/同步状态监控+手动重发 |
| GET /users/directory | view_booking | 参会人搜索(代理/镜像 identity users,注意 listAll 防分页截断) |

## 7. 前端页面

1. **Rooms**(落地页):日期时间+人数+设备筛选;卡片/列表展示,app 级 StatusBadge 六状态;60s 轮询
2. **Room Detail**:信息/图片/当天时间轴/未来 7 天/下一场会议/一键预订
3. **New Booking**:主题、说明、参会人多选(通讯录搜索)、视频设备需求、周期规则;提交前实时冲突提示与替代推荐;自定义下拉浮层一律 createPortal 到 body
4. **My Bookings**:列表+改期+取消(周期系列整体取消带确认)
5. **Admin**(manage_meeting_rooms):Rooms 管理(CRUD/导入/图片上传)、All Bookings(含强制取消/导出)、Settings(规则+SMTP)、Notifications(同步状态+重发)

UI 约定:全英文文案、PdfPreview 不涉及、Decimal 无、日期时间选择用原生或既有组件、Portal chrome 不适用(独立模块用自有 AppLayout,参照 VMS/finance)。

## 8. V1 范围边界

**包含**:PRD 第一阶段 MVP 全部(配置管理/查询预订/状态展示/Available 提示/邮件通知/Outlook 邀请创建更新取消)+ 基础周期会议(固定规则、整系列创建/取消)。

**不包含(二期+)**:统计仪表盘(利用率/热门排行/平均时长)、周期会议逐次修改与例外日期、管理员批量操作增强、Outlook 插件、移动端、回复状态回写、IoT/门禁/签到。V1 管理后台的"统计"仅为预订记录列表+CSV 导出+通知/同步状态,已覆盖 PRD 全部验收标准。

## 9. 部署

- docker-compose dev/prod 各加 booking-api(8010)+ booking-frontend(5178)
- Caddyfile 加 `booking.<域名>` 子域;LE 通配证书已覆盖
- Dockerfile 必须 ARG+ENV 声明全部 VITE_*(VITE_API_URL、VITE_IDENTITY_URL、VITE_EPMS_URL、VITE_PORTAL_URL 等)
- 发布走标准流程:全部镜像(13+2)串行 build+push 同一 TAG,不能只建新增的
- 新 alembic 链上生产用 migrate-prod.sh;btree_gist 扩展需 superuser 或提前在生产库开启

## 10. 测试

- **后端 pytest**(本地 docker uniops_postgres,POSTGRES_* 覆盖):排他约束并发冲突、周期展开与整系列冲突失败、iCal 生成(UID/SEQUENCE/METHOD 断言)、推荐排序、状态计算、权限门禁、SMTP 未配置降级
- **前端**:`tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` typecheck
- **冒烟**:必须覆盖成功路径(创建→收邀请→修改→取消全链路),Outlook Classic 真机验证 text/calendar 渲染

## 11. 风险

- 邮件网关对 text/calendar/.ics 兼容性 → 上线前用真实网关+Outlook Classic 实测;organizer_mode 提供退路
- 周期 VEVENT 与 DB 展开记录一致性 → 整系列操作都在单事务内
- btree_gist 生产库扩展权限 → 迁移前确认;不行则退方案 B(SELECT FOR UPDATE)
- 通讯录数据质量(users 邮箱缺失)→ 参会人选择时过滤无邮箱用户并提示
