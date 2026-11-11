#!/usr/bin/env bash
set -euo pipefail

# Storage rules for the PVCs that an operator renders from a custom resource.
# check_pods cannot see these PVCs, because they exist only after the operator
# runs (aw#265). Prints one line per problem.
#
# Usage: check-cr-storage.sh <rendered.json> <cr-storage.yaml> <repo> <pools-json> <hyperdisk-regex>
#   rendered.json   JSON array of the rendered manifests
#   cr-storage.yaml rows of infra/gke-test/cr-storage.yaml
#   pools-json      object: pool name -> pools.yaml row
# A pool that takes only Hyperdisk needs storage class dynamic-rwo and a size
# of at least 4 GiB (the Hyperdisk Balanced minimum).

rendered="$1" config="$2" repo="$3" pools_json="$4" hd="$5"

rows="$(yq -o=json -I=0 '.crs // [] | map(select(.repo == "'"$repo"'"))' "$config")"

jq -r --argjson rows "$rows" --argjson pools "$pools_json" --arg hd "$hd" '
  # Quantity -> bytes. Empty or unknown -> null.
  def bytes:
    if . == null then null
    elif type == "number" then .
    else
      (capture("^(?<n>[0-9]+(\\.[0-9]+)?)(?<u>Ki|Mi|Gi|Ti|Pi|k|K|M|G|T|P)?$") // null) as $m
      | if $m == null then null
        else ($m.n | tonumber) * ({"Ki":1024,"Mi":1048576,"Gi":1073741824,"Ti":1099511627776,
              "Pi":1125899906842624,"k":1000,"K":1000,"M":1000000,"G":1000000000,
              "T":1000000000000,"P":1000000000000000}[$m.u // ""] // 1)
        end
    end;
  .[] as $o
  | $rows[] | select(.kind == $o.kind) as $r
  | "\($o.kind)/\($o.metadata.name)" as $w
  | (if $r.pool == null then null else $o | getpath($r.pool) end) as $pool
  | ($o | getpath($r.storage_class)) as $class
  | ($o | getpath($r.size)) as $size
  | select($pool == null or $pool == "" or ($pools[$pool] != null and ($pools[$pool].machine_type | test($hd))))
  | (if $class != "dynamic-rwo"
       then "\($w): \($r.storage_class | join(".")) is \(if $class == null or $class == "" then "not set (the PVC gets standard-rwo)" else $class end), but its pool takes only Hyperdisk; set \($r.storage_class | join(".")): dynamic-rwo"
       else empty end),
    (if ($size | bytes) == null
       then "\($w): \($r.size | join(".")) is \($size // "not set"), which is not a size; Hyperdisk Balanced needs at least 4Gi"
     elif ($size | bytes) < 4294967296
       then "\($w): \($r.size | join(".")) is \($size), but Hyperdisk Balanced needs at least 4Gi"
       else empty end)
' "$rendered"
