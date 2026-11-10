#!/usr/bin/env bash
set -euo pipefail

# Find a reason why pods in the test namespace can never start. Used by run.sh
# to fail fast instead of waiting for the full rollout or Job timeout (aw#222).
#
# Usage: blockers.sh <events.json> <pods.json> <pvcs.json> <grace-seconds> <now-epoch> [<scheduling-grace-seconds>]
#   The three files are the output of `kubectl get events|pods|pvc -o json`.
# Prints one line per blocker. Exit 0 when it prints nothing, so the caller
# decides by the output, not by the status. Signals (each needs an object older
# than the grace time, so a normal scale-up from 0 nodes is never a blocker):
#   1. a PVC that is not Bound has a ProvisioningFailed event with "infeasible"
#   2. a Pending pod has a NotTriggerScaleUp event and no TriggeredScaleUp
#      event, or the annotation cluster_autoscaler_unhelpable_until=Inf
#   3. a Pending pod with no node has a FailedScheduling event and no
#      TriggeredScaleUp event
# Signal 3 uses its own, longer grace time (default 300 s). The autoscaler
# sends TriggeredScaleUp for one pod only; the other Pending pods wait for the
# same new node and have no event of their own. So signals 2 (NotTriggerScaleUp)
# and 3 do not count while any pod of the namespace has a TriggeredScaleUp
# event from the last 600 s: that is a scale-up in progress. The annotation
# unhelpable_until=Inf does not depend on it.

if [[ $# -ne 5 && $# -ne 6 ]]; then
  echo "usage: blockers.sh <events.json> <pods.json> <pvcs.json> <grace-seconds> <now-epoch> [<scheduling-grace-seconds>]" >&2
  exit 2
fi
events="$1" pods="$2" pvcs="$3" grace="$4" now="$5" sgrace="${6:-300}"
scaleup_window=600

jq -r --slurpfile pods "$pods" --slurpfile pvcs "$pvcs" \
  --argjson grace "$grace" --argjson sgrace "$sgrace" --argjson now "$now" \
  --argjson window "$scaleup_window" '
  def secs: sub("\\.[0-9]+"; "") | fromdateiso8601;
  def age: ($now - (.metadata.creationTimestamp | secs));
  def old: age >= $grace;
  def sold: age >= $sgrace;
  def evtime: (.lastTimestamp // .eventTime // .metadata.creationTimestamp // null);
  def on($kind; $name): select(.involvedObject.kind == $kind and .involvedObject.name == $name);
  (.items // []) as $ev
  | ($pvcs[0].items // []) as $claims
  | ($pods[0].items // []) as $podlist
  | ([$ev[] | select(.reason == "TriggeredScaleUp" and evtime != null
                     and ($now - (evtime | secs)) <= $window)] | length > 0) as $scaling_now
  | (
      # 1. infeasible PVC
      ($claims[] | select(.status.phase != "Bound" and old) | . as $c
        | $ev[] | on("PersistentVolumeClaim"; $c.metadata.name)
        | select(.reason == "ProvisioningFailed" and ((.message // "") | test("infeasible"; "i")))
        | "PVC \($c.metadata.name): ProvisioningFailed (infeasible): \(.message)"),
      # 2 and 3. pods that cannot be placed
      ($podlist[] | select(.status.phase == "Pending" and (.spec.nodeName // "") == "") | . as $p
        | ([$ev[] | on("Pod"; $p.metadata.name)]) as $pe
        | ($scaling_now or ($pe | any(.reason == "TriggeredScaleUp"))) as $scaling
        | (
            (if old and ($p.metadata.annotations // {} | to_entries
                 | any((.key | test("unhelpable_until")) and .value == "Inf"))
             then "pod \($p.metadata.name): the autoscaler marked it unhelpable (cluster_autoscaler_unhelpable_until=Inf)"
             else empty end),
            (if old and ($pe | any(.reason == "NotTriggerScaleUp")) and ($scaling | not)
             then "pod \($p.metadata.name): NotTriggerScaleUp: \([$pe[] | select(.reason == "NotTriggerScaleUp") | .message][-1])"
             else empty end),
            (if ($p | sold) and ($pe | any(.reason == "FailedScheduling")) and ($scaling | not)
             then "pod \($p.metadata.name): FailedScheduling for \(age)s with no TriggeredScaleUp: \([$pe[] | select(.reason == "FailedScheduling") | .message][-1])"
             else empty end)
          )
      )
    )
' "$events"
