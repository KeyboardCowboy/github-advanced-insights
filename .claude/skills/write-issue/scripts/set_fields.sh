#!/bin/bash
#
# set_fields.sh <issue> <Issue Type> [Facet[,Facet]] [Status]
#
# Sets an issue's project board fields, adding it to the board first if it is
# not there. Newly filed issues are not added automatically, so a plain field
# update fails on them with a confusing empty-lookup error.
#
#   set_fields.sh 42 "Bug" "UI" "Backlog"
#   set_fields.sh 42 "Feature Request" "UI,Arch/DevOps" "In Progress"
#
set -euo pipefail

ISSUE=${1:?issue number required}
TYPE=${2:?issue type required}
FACETS=${3:-}
STATUS=${4:-}

OWNER=KeyboardCowboy
REPO=github-advanced-insights
PROJECT=1

META=$(gh api graphql -f query="
{ user(login: \"$OWNER\") { projectV2(number: $PROJECT) {
    id
    typeField:   field(name: \"Issue Type\") { ... on ProjectV2SingleSelectField { id options { id name } } }
    statusField: field(name: \"Status\")     { ... on ProjectV2SingleSelectField { id options { id name } } }
    facetField:  field(name: \"Facet\")      { ... on ProjectV2MultiSelectField  { id multiSelectOptions { id name } } }
    items(first: 100) { nodes { id content { ... on Issue { number } } } } } } }")

pick() { echo "$META" | ISSUE="$ISSUE" python3 -c "$1"; }

PID=$(pick "import json,sys;print(json.load(sys.stdin)['data']['user']['projectV2']['id'])")

ITEM=$(pick "
import json,sys,os
d = json.load(sys.stdin)['data']['user']['projectV2']
n = int(os.environ['ISSUE'])
print(next((i['id'] for i in d['items']['nodes']
            if (i.get('content') or {}).get('number') == n), ''))")

if [ -z "$ITEM" ]; then
  CONTENT=$(gh api graphql -f query="
  { repository(owner: \"$OWNER\", name: \"$REPO\") { issue(number: $ISSUE) { id } } }" \
    --jq '.data.repository.issue.id')
  ITEM=$(gh api graphql -f query="
  mutation { addProjectV2ItemById(input: {projectId: \"$PID\", contentId: \"$CONTENT\"})
    { item { id } } }" --jq '.data.addProjectV2ItemById.item.id')
  echo "  #$ISSUE added to the board"
fi

# Single-select fields take one option id; the multi-select takes a list.
set_single() {  # <fieldKey> <optionName>
  local key=$1 want=$2
  local fid oid
  fid=$(pick "import json,sys;print(json.load(sys.stdin)['data']['user']['projectV2']['$key']['id'])")
  oid=$(echo "$META" | WANT="$want" python3 -c "
import json,sys,os
d = json.load(sys.stdin)['data']['user']['projectV2']['$key']
w = os.environ['WANT']
match = next((o['id'] for o in d['options'] if o['name'] == w), '')
if not match:
    sys.exit(f\"unknown value {w!r}; expected one of: \" + ', '.join(o['name'] for o in d['options']))
print(match)")
  gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input: {
    projectId: \"$PID\", itemId: \"$ITEM\", fieldId: \"$fid\",
    value: { singleSelectOptionId: \"$oid\" } }) { projectV2Item { id } } }" >/dev/null
}

set_single typeField "$TYPE"
[ -n "$STATUS" ] && set_single statusField "$STATUS"

if [ -n "$FACETS" ]; then
  FID=$(pick "import json,sys;print(json.load(sys.stdin)['data']['user']['projectV2']['facetField']['id'])")
  OIDS=$(echo "$META" | WANT="$FACETS" python3 -c "
import json,sys,os
d = json.load(sys.stdin)['data']['user']['projectV2']['facetField']
want = [w.strip() for w in os.environ['WANT'].split(',')]
known = {o['name']: o['id'] for o in d['multiSelectOptions']}
missing = [w for w in want if w not in known]
if missing:
    sys.exit(f\"unknown facet(s) {missing}; expected from: \" + ', '.join(known))
print(json.dumps([known[w] for w in want]))")
  gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input: {
    projectId: \"$PID\", itemId: \"$ITEM\", fieldId: \"$FID\",
    value: { multiSelectOptionIds: $OIDS } }) { projectV2Item { id } } }" >/dev/null
fi

printf "  #%-4s %-16s %-18s %s\n" "$ISSUE" "$TYPE" "${FACETS:-—}" "${STATUS:-—}"
