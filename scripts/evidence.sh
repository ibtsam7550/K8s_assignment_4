#!/usr/bin/env bash
# Collects the command output the report needs and saves it in results/.
#   scripts/evidence.sh envinfo   machine + tool versions
#   scripts/evidence.sh m2        kubectl get all,pvc,configmap / rs.status() / mongo-init log
#   scripts/evidence.sh rs        replica-set members and states
#   scripts/evidence.sh primary   name of the current PRIMARY pod
#   scripts/evidence.sh count     number of documents in emidb.calculations
#   scripts/evidence.sh persist   delete mongo-1 and prove the data survived (Milestone 2.8)
#   scripts/evidence.sh failover  delete the PRIMARY pod and watch the election (Milestone 3.4)
set -uo pipefail
cd "$(dirname "$0")/.." && mkdir -p results
NS=emi
URI='mongodb://mongo-0.mongo:27017,mongo-1.mongo:27017,mongo-2.mongo:27017/emidb?replicaSet=rs0'

msh() { local pod=$1; shift; kubectl exec -n "$NS" "$pod" -- mongosh --quiet "$@" 2>/dev/null; }
# run a mongosh command on the first mongo pod that answers (some may be down during tests)
on_any() { for p in mongo-0 mongo-1 mongo-2; do msh "$p" "$@" && return 0; done; return 1; }

rs() { on_any --eval 'rs.status().members.forEach(m => print(m.name + "  " + m.stateStr))' || echo "replica set not reachable"; }
primary() { local h; h=$(on_any --eval 'print(db.hello().primary || "")') && [ -n "$h" ] && echo "${h%%.*}"; }
count() { on_any "$URI" --eval 'print(db.calculations.countDocuments({}))'; }

envinfo() {
  { date
    sw_vers 2>/dev/null
    echo "CPU: $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
    echo "RAM: $(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f GB", $1/2^30}')"
    docker --version; minikube version | head -1; kubectl version --client; python3 --version
    httperf --version 2>&1 | grep -iv warning | head -1
  } > results/env.txt 2>&1
  cat results/env.txt
}

m2() {
  kubectl get all,pvc,configmap -n "$NS" -o wide | tee results/m2_get_all.txt
  { echo "== rs.status() members =="; rs; } | tee results/m2_rs_status.txt
  kubectl logs job/mongo-init -n "$NS" | tee results/m2_mongo_init_log.txt
}

persist() {
  {
    echo "== BEFORE =="; date
    kubectl get pod mongo-1 -n "$NS" -o wide
    kubectl get pvc data-mongo-1 -n "$NS"
    echo "pod uid:     $(kubectl get pod mongo-1 -n "$NS" -o jsonpath='{.metadata.uid}')"
    echo "volume (PV): $(kubectl get pvc data-mongo-1 -n "$NS" -o jsonpath='{.spec.volumeName}')"
    echo "documents in emidb.calculations: $(count)"
    echo; echo "== kubectl delete pod mongo-1 =="
    kubectl delete pod mongo-1 -n "$NS"
    until kubectl get pod mongo-1 -n "$NS" >/dev/null 2>&1; do sleep 1; done
    kubectl wait --for=condition=Ready pod/mongo-1 -n "$NS" --timeout=180s
    for _ in $(seq 1 45); do rs | grep -qE 'mongo-1.mongo:27017 +(SECONDARY|PRIMARY)' && break; sleep 2; done
    echo; echo "== AFTER =="; date
    kubectl get pod mongo-1 -n "$NS" -o wide
    kubectl get pvc data-mongo-1 -n "$NS"
    echo "pod uid:     $(kubectl get pod mongo-1 -n "$NS" -o jsonpath='{.metadata.uid}')   (new pod, same name)"
    echo "volume (PV): $(kubectl get pvc data-mongo-1 -n "$NS" -o jsonpath='{.spec.volumeName}')   (same volume)"
    echo "documents in emidb.calculations: $(count)"
    echo "documents read from mongo-1 itself: $(msh mongo-1 --eval 'db.getMongo().setReadPref("secondaryPreferred"); print(db.getSiblingDB("emidb").calculations.countDocuments({}))')"
    echo "replica set:"; rs
  } 2>&1 | tee results/m2_persistence.txt
}

failover() {
  {
    local p; p=$(primary) || { echo "cannot find the PRIMARY (is the replica set up?)"; exit 1; }
    echo "PRIMARY before: $p"; date
    echo "documents before: $(count)"
    kubectl delete pod "$p" -n "$NS" --wait=false
    for i in $(seq 1 20); do
      sleep 3
      echo "t+$((i * 3))s  $(on_any --eval 'print(rs.status().members.map(m => m.name.split(".")[0] + ":" + m.stateStr).join("  "))' || echo "no answer")"
    done
    echo "PRIMARY after: $(primary)"
    echo "documents after: $(count)"
  } 2>&1 | tee results/m3_primary_failover.txt
}

case "${1:-}" in
  envinfo) envinfo ;; m2) m2 ;; rs) rs ;; primary) primary ;; count) count ;;
  persist) persist ;; failover) failover ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
