#!/usr/bin/env bash
set -euo pipefail

# Find a reason why pods in the test namespace can never start. Used by run.sh
# to fail fast instead of waiting for the full rollout or Job timeout (aw#222).
#
# Usage: blockers.sh <events.json> <pods.json> <pvcs.json> <grace-seconds> <now-epoch>
#   The three files are the output of `kubectl get events|pods|pvc -o json`.
# Prints one line per blocker. Exit 0 when it prints nothing, so the caller
# decides by the output, not by the status. Signals (each needs an object older
# than the grace time, so a normal scale-up from 0 nodes is never a blocker):
#   1. a PVC that is not Bound has a ProvisioningFailed event with "infeasible"
#   2. a Pending pod has a NotTriggerScaleUp event and no TriggeredScaleUp
#      event, or the annotation cluster_autoscaler_unhelpable_until=Inf
#   3. a Pending pod with no node has a FailedScheduling event and no
#      TriggeredScaleUp event

if [[ $# -ne 5 ]]; then
  echo "usage: blockers.sh <events.json> <pods.json> <pvcs.json> <grace-seconds> <now-epoch>" >&2
  exit 2
fi
events="$1" pods="$2" pvcs="$3" grace="$4" now="$5"

jq -r --slurpfile pods "$pods" --slurpfile pvcs "$pvcs" \
  --argjson grace "$grace" --argjson now "$now" '
  def age: ($now - (.metadata.creationTimestamp | fromdateiso8601));
  def old: age >= $grace;
  def on($kind; $name): select(.involvedObject.kind == $kind and .involvedObject.name == $name);
  (.items // []) as $ev
  | ($pvcs[0].items // []) as $claims
  | ($pods[0].items // []) as $podlist
  | (
      # 1. infeasible PVC
      ($claims[] | select(.status.phase != "Bound" and old) | . as $c
        | $ev[] | on("PersistentVolumeClaim"; $c.metadata.name)
        | select(.reason == "ProvisioningFailed" and ((.message // "") | test("infeasible"; "i")))
        | "PVC \($c.metadata.name): ProvisioningFailed (infeasible): \(.message)"),
      # 2 and 3. pods that cannot be placed
      ($podlist[] | select(.status.phase == "Pending" and (.spec.nodeName // "") == "" and old) | . as $p
        | ([$ev[] | on("Pod"; $p.metadata.name)]) as $pe
        | ($pe | any(.reason == "TriggeredScaleUp")) as $scaling
        | (
            (if ($p.metadata.annotations // {} | to_entries
                 | any((.key | test("unhelpable_until")) and .value == "Inf"))
             then "pod \($p.metadata.name): the autoscaler marked it unhelpable (cluster_autoscaler_unhelpable_until=Inf)"
             else empty end),
            (if ($pe | any(.reason == "NotTriggerScaleUp")) and ($scaling | not)
             then "pod \($p.metadata.name): NotTriggerScaleUp: \([$pe[] | select(.reason == "NotTriggerScaleUp") | .message][-1])"
             else empty end),
            (if ($pe | any(.reason == "FailedScheduling")) and ($scaling | not)
             then "pod \($p.metadata.name): FailedScheduling for \(age)s with no TriggeredScaleUp: \([$pe[] | select(.reason == "FailedScheduling") | .message][-1])"
             else empty end)
          )
      )
    )
' "$events"
