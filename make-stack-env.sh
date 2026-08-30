#!/usr/bin/env bash
# 生成一套栈的 .env。用法: ./make-stack-env.sh <索引> <LAN_HOST> [基础.env]
#   索引 0 = 主环境(标准端口, 同事访问)   索引 1..N = 各开发者的栈(端口 +i*100)
# 例:  ./make-stack-env.sh 0 10.10.50.70            > uniops-prod/.env
#      ./make-stack-env.sh 1 10.10.50.70            > .env.dev1
set -euo pipefail
i="${1:?用法: make-stack-env.sh <索引> <LAN_HOST> [基础.env]}"
host="${2:?第二个参数是服务器 IP}"
base="${3:-.env}"
off=$((i * 100))
prefix=$([ "$i" = 0 ] && echo "uniops-test" || echo "uniops-dev$i")

cat "$base"
cat <<EOF

# ─────────────────────────────────────────────────────────────────────────────
# 栈 $prefix —— 由 make-stack-env.sh $i $host 生成
# 端口 = 标准端口 + $off。VITE_* 里的端口与 ports: 映射用同一批变量, 不会错位。
# LAN_HOST 必须是服务器 IP: 使用者的浏览器在他们自己的电脑上, localhost 对他们是错的。
# ─────────────────────────────────────────────────────────────────────────────
STACK_PREFIX=$prefix
LAN_HOST=$host

POSTGRES_PORT=$((5432 + off))
REDIS_PORT=$((6379 + off))

EPMS_API_PORT=$((8000 + off))
MDM_API_PORT=$((8002 + off))
APPROVAL_API_PORT=$((8003 + off))
FINANCE_API_PORT=$((8004 + off))
FILE_API_PORT=$((8005 + off))
EXPENSE_API_PORT=$((8006 + off))
BUDGET_API_PORT=$((8007 + off))
VMS_API_PORT=$((8008 + off))
IDENTITY_API_PORT=$((8009 + off))
BOOKING_API_PORT=$((8010 + off))
MRP_API_PORT=$((8011 + off))
EHS_API_PORT=$((8012 + off))

EPMS_PORT=$((5173 + off))
PORTAL_PORT=$((5174 + off))
OA_PORT=$((5175 + off))
VMS_PORT=$((5176 + off))
FINANCE_PORT=$((5177 + off))
BOOKING_PORT=$((5178 + off))
MRP_PORT=$((5179 + off))
EHS_PORT=$((5180 + off))

MAILHOG_SMTP_PORT=$((1025 + off))
MAILHOG_UI_PORT=$((8025 + off))

# 机械盘阵列上的 postgres 调优(见 docs/superpowers/specs/2026-08-25-*)
PG_SHARED_BUFFERS=2GB
PG_EFFECTIVE_CACHE=8GB
EOF
