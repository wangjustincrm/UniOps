# 发布 `d2dceb6` — MRP Inventory / 采购建议 / 计划版本 / 最小批量

上一个生产版本 `179c865`。本次只增加 MRP 这一条线的改动 —— `179c865` 上那四个别人的合并**已经在生产上**，不会被重新引入。

**7 个迁移**：`mrp11`–`mrp15`、epms `ah01`、mdm `0017`。
**17 个镜像**已构建并推送，registry 侧已逐个反查确认 `:d2dceb6` 全部存在。

（镜像实际构建于 `718dd44`；`d2dceb6` 只多了这份发布文档、不含任何服务代码，
两个标签指向同一份镜像，都已推送。）

七个前端镜像烤入的域名与当前生产版本 `179c865` **逐字节相同**，已 diff 确认 ——
那个裸 `http://localhost` 两版都有，是既有债，不是本次引入。

---

## 在应用服务器上（`10.10.50.65`，全程 sudo）

### 1. 拉代码

```bash
cd /opt/uniops
sudo git pull origin main
git log --oneline -1        # 应显示 d2dceb6
```

### 2. 先备份数据库

本次有 7 个迁移，其中 mdm/epms 动的是共享库。**别省这一步。**
生产库不在这台机器上（`DB_HOST=10.10.50.20`），prod compose 里没有 postgres 服务，只能用一次性容器：

```bash
DB_HOST=$(grep -m1 '^DB_HOST=' .env | sed 's/^DB_HOST=//; s/[[:space:]]*#.*$//')
DB_PASSWORD=$(grep -m1 '^DB_PASSWORD=' .env | sed 's/^DB_PASSWORD=//; s/[[:space:]]*#.*$//')
sudo docker run --rm --network host -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  pg_dump -h "$DB_HOST" -U epms -d epms | gzip > ~/bk-$(date +%F-%H%M).sql.gz
gzip -t ~/bk-*.sql.gz && echo BACKUP_OK
```

`pg_dump` 失败时 `gzip` 照样会产出一个看着正常的小文件，所以 `gzip -t` 那句必须跑。

### 3. ⛔ 前置检查：mdm 的 alembic 版本（**不通过就停**）

```bash
sudo docker run --rm --network host -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  psql -h "$DB_HOST" -U epms -d epms -c "select * from alembic_version_mdm;"
```

- **显示 `0016_nc_sync_state`** → 正常，继续第 4 步。
- **显示 `0016` 以外的任何值**（尤其 `0006_partner_remittance_email`）→ **停下来，先别跑迁移**，把结果发我。

为什么要查：本地 dev 库的这张表停在 `0006`，而迁移目录已到 `0017`。在那种状态下 `alembic upgrade head` 会从 `0007` 开始重跑，第一句就撞 `relation "materials" already exists` —— dev 上已经真实发生过一次。生产大概率是好的（`0007`–`0016` 都随以前的发布上过线，否则那些发布当时就会失败），但"大概率"不足以拿生产库赌，而我没有生产库凭据、查不到。

### 4. 改 TAG 并拉镜像

```bash
sudo sed -i "s/^TAG=.*/TAG=d2dceb6/" .env
grep '^TAG=' .env                                   # 确认改到了
sudo docker compose -f docker-compose.prod.yml pull  # ★ pull 千万别带 --profile edge
```

### 5. 跑迁移

```bash
sudo ./migrate-prod.sh
```

顺序是脚本自带的（finance → mdm → epms → … → mrp）。**mdm 必须在 epms 之前**，脚本已经保证。
`mrp-api` 在列表里，`mrp11`–`mrp15` 会被应用。

任何一步报错就**停下**，把输出发我，别继续 `up -d`。

### 6. 起容器

```bash
sudo docker compose -f docker-compose.prod.yml --profile edge up -d
sudo docker compose -f docker-compose.prod.yml ps
```

`--profile edge` 只加在 `up -d`（Caddy 在这个 profile 里）。两条命令都要显式 `-f docker-compose.prod.yml`。

本次**没有改 Caddyfile**，所以不需要 `restart edge`。

---

## 发布后自检

```bash
# 三个新迁移真的落库了
sudo docker run --rm --network host -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  psql -h "$DB_HOST" -U epms -d epms -c \
  "select 'mdm' t, * from alembic_version_mdm
   union all select 'mrp', * from alembic_version_mrp
   union all select 'epms', * from alembic_version;"
# 期望：mdm=0017_material_acct_group  mrp=mrp15_wms_lot_uom  epms=ah01_po_line_planned_arrival

# 新列都在
sudo docker run --rm --network host -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  psql -h "$DB_HOST" -U epms -d epms -c \
  "select table_name, column_name from information_schema.columns
   where (table_name='po_line_items' and column_name='planned_arrival_date')
      or (table_name='materials' and column_name='erp_class_code')
      or (table_name='wms_inventory_lots' and column_name='uom')
      or table_name='wms_lot_locations' order by 1,2;"
```

浏览器：`https://mrp.canadaroyalmilk.com` → 左侧栏应出现 **Inventory**，Production Plan 右侧应出现 **How this plan was built**，周表头应显示 **2026-W32** 这种年周号。

---

## 上线后要补的两件事（不阻塞发布）

**① 物料分类回填**。mdm `0017` 迁移自带从 `raw_payload` 回填，所以 `materials.erp_class_code` 上线即有值，不需要重跑 ERP 同步。**这条不用做，写在这里是让你知道不用做。**

**② ERP 到货日期回填**。生产上 `po_line_items.planned_arrival_date` 会是空的（新列）。跑：

```bash
cd /opt/uniops
sudo docker compose -f docker-compose.prod.yml run --rm --no-deps epms-api \
  python -m scripts.backfill_po_planned_arrival --dry-run
# 看清楚要改多少行，没问题再去掉 --dry-run 跑一次
```

**不要用 NC 同步的 `full` 模式去填这一列** —— 那个模式会 `delete from purchase_orders where source='nc'` 再重建，为一个日期列把所有 NC 采购单和收货单连 id 一起换掉。上面这个脚本只 UPDATE 那一列。

**③ 供应参数还没录**。dev 上 `material_suppliers` 只有 2 个物料有提前期。1C 的采购建议靠提前期倒推下单日，不录的话绝大多数建议只会显示 "lead time missing"。页面在 MRP → Supply Parameters，支持 Excel 粘贴。

---

## 回滚

```bash
sudo sed -i "s/^TAG=.*/TAG=179c865/" .env
sudo docker compose -f docker-compose.prod.yml pull
sudo docker compose -f docker-compose.prod.yml --profile edge up -d
```

迁移是前向的，一般不回滚库。这 7 个迁移都是**新增**（加列、加表），没有删改既有列，所以旧镜像配新库能正常跑 —— 回滚镜像不需要回滚库。
