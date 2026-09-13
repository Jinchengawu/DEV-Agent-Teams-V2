#!/usr/bin/env bash
set -euo pipefail

app_id_service="agent-team-os.feishu.app-id"
app_secret_service="agent-team-os.feishu.app-secret"

restore_echo() {
  stty echo 2>/dev/null || true
}
trap restore_echo EXIT INT TERM

printf 'Feishu App ID（输入后回车）: '
IFS= read -r feishu_app_id
feishu_app_id="${feishu_app_id//\\_/_}"
printf 'Feishu App Secret（输入不回显，输入后回车）: '
stty -echo
IFS= read -r feishu_app_secret
stty echo
printf '\n'

if [[ ! "$feishu_app_id" =~ ^cli_[A-Za-z0-9]+$ ]]; then
  printf '未保存：App ID 格式应为 cli_xxx，请勿包含反斜杠或空格。\n' >&2
  exit 2
fi
if [[ -z "$feishu_app_secret" ]]; then
  printf '未保存：App Secret 为空。\n' >&2
  exit 2
fi

security add-generic-password -U -a agent-team-os \
  -s "$app_id_service" -w "$feishu_app_id" >/dev/null
security add-generic-password -U -a agent-team-os \
  -s "$app_secret_service" -w "$feishu_app_secret" >/dev/null
security find-generic-password -s "$app_id_service" >/dev/null
security find-generic-password -s "$app_secret_service" >/dev/null
unset feishu_app_id feishu_app_secret

printf '已保存至 macOS Login Keychain，请在 Tenant Connection 中使用：\n'
printf '  App ID Reference: keychain:%s\n' "$app_id_service"
printf '  App Secret Reference: keychain:%s\n' "$app_secret_service"
