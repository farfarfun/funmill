#!/usr/bin/env bash
set -euo pipefail

: "${FUNMILL_API_KEY:?set FUNMILL_API_KEY to the value in .env}"

command -v curl >/dev/null || { printf 'curl is required\n' >&2; exit 1; }
command -v jq >/dev/null || { printf 'jq is required\n' >&2; exit 1; }

base_url="${FUNMILL_URL:-http://localhost:8812}"
api_url="${base_url%/}/v1"
callback_url="${FUNMILL_CALLBACK_URL:-}"
smoke_timeout="${FUNMILL_SMOKE_TIMEOUT:-120}"
if [[ ! "$smoke_timeout" =~ ^[1-9][0-9]*$ ]]; then
  printf 'FUNMILL_SMOKE_TIMEOUT must be a positive integer\n' >&2
  exit 1
fi

request() {
  local method="$1"
  local path="$2"
  shift 2
  curl --fail --silent --show-error \
    -X "$method" "${api_url}/${path}" \
    -H "X-API-Key: ${FUNMILL_API_KEY}" \
    -H 'Content-Type: application/json' \
    "$@"
}

task_id() {
  jq -er '.task_id | select(type == "string" and length > 0)'
}

wait_for() {
  local id="$1"
  local started="$SECONDS"
  while true; do
    local state status progress
    state="$(request GET "tasks/${id}")"
    status="$(jq -r .status <<<"$state")"
    progress="$(request GET "tasks/${id}/progress" | jq -r '.progress // "-"')"
    printf 'task_id=%s status=%s progress=%s\n' "$id" "$status" "$progress"
    case "$status" in
      succeeded) return ;;
      failed|canceled)
        jq . <<<"$state" >&2
        return 1
        ;;
    esac
    if (( SECONDS - started >= smoke_timeout )); then
      printf 'Timed out waiting for %s\n' "$id" >&2
      return 1
    fi
    sleep 1
  done
}

task_payload="$(jq -n --arg callback_url "$callback_url" '
{
  language: "python",
  source: "import time\nfrom wmill import set_progress\n\ndef main(name: str = \"Funmill\"):\n    for progress in (25, 50, 75, 99):\n        print(f\"progress={progress}\")\n        set_progress(progress)\n        time.sleep(0.2)\n    return {\"message\": f\"hello {name}\"}\n",
  args: {name: "Funmill"},
  retry: {attempts: 2, delay_seconds: 1},
  callback_url: (if $callback_url == "" then null else $callback_url end)
}')"

single_id="$(request POST tasks --data-binary "$task_payload" | task_id)"
printf 'single_task_id=%s\n' "$single_id"
wait_for "$single_id"
request GET "tasks/${single_id}/logs" | jq .
request GET "tasks/${single_id}/result" | jq .

workflow_payload="$(jq -n --arg callback_url "$callback_url" '
{
  tasks: [
    {
      key: "a",
      language: "python",
      source: "def main():\n    print(\"A finished\")\n    return 7\n"
    },
    {
      key: "b",
      language: "python",
      source: "def main():\n    print(\"B finished\")\n    return \"B\"\n",
      depends_on: ["a"]
    },
    {
      key: "c",
      language: "bash",
      source: "main() { echo C finished; }\n",
      depends_on: ["a"]
    }
  ],
  callback_url: (if $callback_url == "" then null else $callback_url end)
}')"

workflow_id="$(request POST workflows --data-binary "$workflow_payload" | task_id)"
printf 'workflow_task_id=%s\n' "$workflow_id"
wait_for "$workflow_id"
request GET "tasks/${workflow_id}/logs" | jq .
request GET "tasks/${workflow_id}/result" | jq .
