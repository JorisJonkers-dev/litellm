#!/usr/bin/env bash
# Proves a LiteLLM budget is enforced and alerted on, live (agent-kit#570). It makes a team-less
# throwaway key with a tiny max_budget and a one-hour budget_duration, calls a cheap model with it
# until the proxy refuses, prints the refusal and the key's budget fields (the window and the
# reset), waits for LiteLLMBudgetExhausted to fire in Prometheus and reach Alertmanager, then
# deletes the key and silences the alert for that alias (the proxy keeps the key's last gauge
# until it restarts, so the alert would otherwise keep firing for a key that no longer exists).
#
# Needs LITELLM_ADMIN_TOKEN, a management key from agent-kit's
# registry/generated/litellm/make-admin-token.sh. Every key goes in a curl config on stdin, so
# none reaches argv. PROMETHEUS and ALERTMANAGER are base URLs this host reaches (a pod address
# on the cluster network, or a kubectl port-forward).
#
#   LITELLM_ADMIN_TOKEN=... PROMETHEUS=http://<pod>:9090 ALERTMANAGER=http://<pod>:9093 tools/budget-probe.sh
set -euo pipefail
: "${LITELLM_ADMIN_TOKEN:?set LITELLM_ADMIN_TOKEN to a management key (make-admin-token.sh)}"
: "${PROMETHEUS:?set PROMETHEUS to the Prometheus base URL}"
: "${ALERTMANAGER:?set ALERTMANAGER to the Alertmanager base URL}"
PROXY=${PROXY:-https://litellm.jorisjonkers.dev}
MODEL=${MODEL:-ministral-3b}
MAX_BUDGET=${MAX_BUDGET:-0.0001}
MAX_CALLS=${MAX_CALLS:-400}
ALIAS=${ALIAS:-budget-probe-$(date -u +%Y%m%dT%H%M%S)}
work=$(mktemp -d)
key=""

# $1 is the bearer; the rest are curl arguments.
call() {
  local bearer=$1
  shift
  curl -sS --config - "$@" <<CFG
header = "Authorization: Bearer ${bearer}"
header = "Content-Type: application/json"
CFG
}
admin() { call "$LITELLM_ADMIN_TOKEN" "$@"; }
json() { python3 -I -c "import json, sys; d = json.load(sys.stdin); print($1)"; }

cleanup() {
  if [ -n "$key" ]; then
    printf '{"keys":["%s"]}' "$key" > "$work/delete.json"
    if admin --fail-with-body --data-binary "@$work/delete.json" "$PROXY/key/delete" > "$work/deleted.json"; then
      echo "deleted the key $ALIAS"
    else
      echo "could not delete the key $ALIAS; delete it in the admin UI" >&2
      cat "$work/deleted.json" >&2
    fi
  fi
  rm -rf "$work"
}
trap cleanup EXIT

printf '{"key_alias":"%s","models":["%s"],"max_budget":%s,"budget_duration":"1h","metadata":{"purpose":"agent-kit#570 budget probe; delete when done"}}' \
  "$ALIAS" "$MODEL" "$MAX_BUDGET" > "$work/new.json"
admin --fail-with-body --data-binary "@$work/new.json" "$PROXY/key/generate" > "$work/key.json"
key=$(json 'd["key"]' < "$work/key.json")
echo "made $ALIAS: max_budget $MAX_BUDGET, budget_duration 1h, model $MODEL, no team"

body='{"model":"'"$MODEL"'","max_tokens":1,"messages":[{"role":"user","content":"ok"}]}'
refused=""
for i in $(seq 1 "$MAX_CALLS"); do
  status=$(call "$key" -o "$work/answer.json" -w '%{http_code}' --data-binary "$body" "$PROXY/v1/chat/completions")
  if [ "$status" != 200 ]; then
    refused=$i
    break
  fi
done
if [ -z "$refused" ]; then
  echo "not refused after $MAX_CALLS calls; raise MAX_CALLS or lower MAX_BUDGET" >&2
  exit 1
fi
echo "refused on call $refused with HTTP $status:"
cat "$work/answer.json"; echo

case "$(cat "$work/answer.json")" in
  *udget*) ;;
  *) echo "the refusal is not a budget refusal; see the answer above" >&2; exit 1 ;;
esac

# The window and the reset as the proxy stores them. The key asks about itself, so its value
# never goes in a query string (the proxy's access log prints those).
call "$key" --fail-with-body "$PROXY/key/info" > "$work/info.json"
echo "key budget fields:"
json '{k: d["info"].get(k) for k in ("key_alias", "spend", "max_budget", "budget_duration", "budget_reset_at", "team_id", "created_at")}' < "$work/info.json"

query() { curl -sS -G --data-urlencode "query=$1" "$PROMETHEUS/api/v1/query" | json '[(r["metric"], r["value"][1]) for r in d["data"]["result"]]'; }
echo "Prometheus series for $ALIAS:"
query "{__name__=~\"litellm_(remaining_api_key_budget|api_key_max_budget|api_key_budget_remaining_hours)_metric\",api_key_alias=\"$ALIAS\"}"

# Scrape, rule evaluation and the rule's for: 1m take a few minutes.
echo "waiting up to 10 minutes for LiteLLMBudgetExhausted{api_key_alias=\"$ALIAS\"}"
state=""
for _ in $(seq 1 60); do
  state=$(query "ALERTS{alertname=\"LiteLLMBudgetExhausted\",api_key_alias=\"$ALIAS\"}")
  case "$state" in *firing*) break ;; esac
  sleep 10
done
echo "ALERTS: $state"
case "$state" in *firing*) ;; *) echo "LiteLLMBudgetExhausted did not fire" >&2; exit 1 ;; esac
echo "Alertmanager:"
curl -sS -G --data-urlencode "filter=api_key_alias=\"$ALIAS\"" "$ALERTMANAGER/api/v2/alerts" |
  json '[(a["labels"]["alertname"], a["status"]["state"], a["startsAt"]) for a in d]'

# The key is deleted on exit; its stale gauge would keep the alert firing until the proxy restarts.
ends=$(date -u -d '+1 day' +%Y-%m-%dT%H:%M:%SZ)
printf '{"matchers":[{"name":"api_key_alias","value":"%s","isRegex":false,"isEqual":true}],"startsAt":"%s","endsAt":"%s","createdBy":"budget-probe.sh","comment":"agent-kit#570: the probe key is deleted; its last gauge stays until the proxy restarts"}' \
  "$ALIAS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ends" > "$work/silence.json"
curl -sS -H 'Content-Type: application/json' --data-binary "@$work/silence.json" "$ALERTMANAGER/api/v2/silences"; echo
