# Loan EMI Calculator on Kubernetes (Minikube) - DS-584 Assignment 4

Flask + MongoDB replica set (StatefulSet) + NGINX Ingress + HPA, on a single-node Minikube cluster.
Written for macOS (Intel) with Docker Desktop.

```
app/        Flask app, static UI, Dockerfile, .dockerignore, requirements.txt
k8s/        00-namespace, 10-config, 20-mongo, 30-mongo-init, 40-web, 50-ingress   (kubectl apply -f k8s/)
k8s/hpa/    hpa.yaml - applied only for the autoscaling test (it overrides `kubectl scale`)
scripts/    load.sh (httperf load)  run_tests.sh  monitor.py  traffic.py  evidence.sh
            + first_page.docx (report cover template) and architecture.png
results/    every output the report needs (CSV, text, graphs, screenshots)
```

Run every command from the project root. Use **one terminal per long-running command** (they are marked *keep open*).

---

## 0. One-time setup

1. Docker Desktop -> Settings -> Resources: give it as much CPU and memory as you can spare (the tests were run with the defaults), Apply & Restart.
2. Tools and one Python virtual environment for everything (Homebrew Python refuses a global `pip install`, so always use the venv):
   ```bash
   brew install minikube kubectl httperf
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r app/requirements.txt -r scripts/requirements.txt
   chmod +x scripts/*.sh scripts/*.py
   ```
   Re-run `source .venv/bin/activate` in every new terminal that runs a Python script.
3. Record versions for the report: `scripts/evidence.sh envinfo`.

## 1. Milestone 1 - run the app locally (screenshot)

```bash
docker run -d --name mongo-local -p 27017:27017 mongo:6.0
MONGO_URL="mongodb://localhost:27017/emidb" PORT=8081 python3 app/app.py          # keep open (venv active)
```
Open http://localhost:8081, enter **500000 / 12 / 24**. Expected: EMI **23,536.74**, total payment 564,881.67, total interest 64,881.67.
Take the screenshot (result panel + history table) -> save as `results/screenshots/m1_ui.png`.
Also try one invalid input (for example rate = 0) and screenshot the red error message -> `results/screenshots/m1_error.png`.

Clean up: Ctrl+C, then `docker rm -f mongo-local`.

## 2. Start Minikube

```bash
minikube start --driver=docker
minikube addons enable ingress
minikube addons enable metrics-server
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s
```
`kubectl top nodes` starts working about a minute after metrics-server is enabled.

## 3. Build the image and load it into Minikube

```bash
docker build -t emi-web:1.0 app/
minikube image load emi-web:1.0
minikube image ls | grep emi-web          # must list emi-web:1.0
```

## 4. Milestone 2 - deploy everything

```bash
kubectl apply -f k8s/
kubectl get pods -n emi -w                # Ctrl+C when: mongo-0/1/2 Running, mongo-init Completed, emi-web x3 1/1 Ready
kubectl logs job/mongo-init -n emi        # ends with one PRIMARY and two SECONDARY
scripts/evidence.sh m2                    # saves results/m2_get_all.txt, m2_rs_status.txt, m2_mongo_init_log.txt
```
The web pods may show `0/1` for the first 1-3 minutes: they become Ready only after the replica set exists. That is expected.

**Port-forward check (M2.7)** - *keep open*:
```bash
kubectl port-forward svc/emi-web 8080:80 -n emi
```
Open http://localhost:8080, make a few calculations, screenshot with history -> `results/screenshots/m2_portforward.png`.
Then `scripts/evidence.sh count` shows the stored documents. (Port-forward talks to one pod only, so it does not show load balancing.)

**Persistence test (M2.8)** - stop the port-forward first, then:
```bash
scripts/evidence.sh persist               # saves results/m2_persistence.txt
```
It prints pod age/UID, PVC `data-mongo-1` and its volume before and after deleting `mongo-1`, plus the document count before and after.

## 5. Milestone 3 - Ingress, load balancing, self-healing

**5.1 Ingress** - *keep open* (asks for your Mac password; Docker driver on macOS needs the tunnel):
```bash
minikube tunnel
```
In another terminal:
```bash
echo "127.0.0.1 emi.local" | sudo tee -a /etc/hosts
curl -i http://emi.local/health           # HTTP 200
```
Open http://emi.local, screenshot -> `results/screenshots/m3_ingress.png`.

**5.2 Load distribution, 3 replicas** (30 requests; per-pod table is printed and saved):
```bash
python3 scripts/traffic.py --label dist_r3 --count 30
```

**5.3 Scale by hand to 5** (watch the pods in a second terminal: `kubectl get pods -n emi -w`):
```bash
kubectl scale deployment emi-web --replicas=5 -n emi
python3 scripts/traffic.py --label dist_r5_early --count 30       # immediately: new pods not Ready yet
kubectl rollout status deployment/emi-web -n emi
python3 scripts/traffic.py --label dist_r5 --count 30             # all 5 Ready
```
If the early run already shows all 5 pods, they simply became Ready quickly - report what you see.
You can leave the Deployment at 5 for the next steps.

**5.4 Self-healing**

(a) Web pod. Terminal A: `kubectl get pods -n emi -w | tee results/m3_watch_webpod.txt`. Terminal B:
```bash
python3 scripts/traffic.py --label selfheal_web --seconds 60
```
Terminal C, a few seconds after B starts:
```bash
kubectl delete $(kubectl get pods -n emi -l app=emi-web -o name | head -1) -n emi
```

(b) MongoDB PRIMARY. Terminal A: `kubectl get pods -n emi -w | tee results/m3_watch_primary.txt`. Terminal B:
```bash
python3 scripts/traffic.py --label selfheal_primary --seconds 90
```
Terminal C, a few seconds after B starts:
```bash
scripts/evidence.sh failover              # deletes the PRIMARY pod, prints member states every 3 s for 60 s
```
A few slow or failed requests during the ~10 s election are possible; report what you actually see.

**5.5 Rolling update and rollback** (Terminal B runs the request loop for the whole time):
```bash
sed -i '' 's/v1\.0/v1.1/' app/static/index.html              # macOS sed: footer now says v1.1
docker build -t emi-web:1.1 app/ && minikube image load emi-web:1.1

# Terminal B - start first:
python3 scripts/traffic.py --label rollout --seconds 200

# Terminal C:
kubectl annotate deployment/emi-web -n emi kubernetes.io/change-cause="footer v1.1" --overwrite
kubectl set image deployment/emi-web web=emi-web:1.1 -n emi
kubectl rollout status deployment/emi-web -n emi | tee results/m3_rollout_status.txt
kubectl rollout undo deployment/emi-web -n emi
kubectl rollout status deployment/emi-web -n emi | tee -a results/m3_rollout_status.txt
kubectl rollout history deployment/emi-web -n emi | tee results/m3_rollout_history.txt

sed -i '' 's/v1\.1/v1.0/' app/static/index.html              # put the source back to v1.0
```
Wait for Terminal B to finish; its last line shows `failed=`.

## 6. Milestone 4 - load tests

`scripts/load.sh` is your `continuous-load.sh` adapted: one httperf run of `POST /api/emi` with random valid inputs (every request writes to MongoDB)
at `RATE` requests/s for `DURATION` seconds against http://emi.local. `scripts/run_tests.sh` calls it for every experiment, starts `monitor.py`
(host CPU every 1 s, `kubectl top pods` every 15 s, with timestamps) around it and saves everything under `results/`.

Check first: `minikube tunnel` is running, `kubectl top pods -n emi` shows numbers, and no HPA exists (`kubectl get hpa -n emi` -> none).
`load.sh` checks `/health` with `curl --resolve` and `traffic.py` resolves `emi.local` once, because on macOS a normal lookup of `*.local` names can add ~5 s per request. `run_tests.sh` stops any leftover `monitor.py` before it starts a new one (two monitors would write into the same CSV files).
Try the generator once: `scripts/load.sh 10 5` (expect `2xx=50` in the report). `caffeinate -i` stops the Mac sleeping during the long runs.

**6.1 Fixed replicas - 15 runs of 60 s, about 20 minutes**
```bash
caffeinate -i scripts/run_tests.sh matrix
```
Results: the httperf reports are in `results/httperf/`, the summary of the 15 runs is `results/fixed_runs.csv`, and the graphs are in `results/graphs/`.

**6.2 Autoscaling - 10 minutes** (2 min at 10 req/s, 5 min at 100 req/s, 3 min at 10 req/s)
```bash
# second terminal, optional independent log:  while true; do date; kubectl get hpa emi-web -n emi; sleep 15; done | tee results/m4_hpa_get.txt
caffeinate -i scripts/run_tests.sh hpa
kubectl describe hpa emi-web -n emi | tee results/m4_hpa_describe.txt   # run this DURING the 100 req/s phase to capture the scaling events
```
The script records the real start/end time of every load phase in `results/autoscale/phases.csv`.
Afterwards remove the HPA: `kubectl delete -f k8s/hpa/hpa.yaml; kubectl scale deployment emi-web -n emi --replicas=3`.

## 7. Report

Export the Word report to PDF with **File -> Export -> PDF** (due **Nov 25, 2026, 11:59 pm**, Google Classroom).

## 8. Push to Git

```bash
git init && git add . && git commit -m "Assignment 4"
git remote add origin <your-repo-url> && git push -u origin main
```
Submit the PDF and the repository link.

## Submission checklist

| Deliverable | File |
|---|---|
| M1 screenshot, source | `results/screenshots/m1_ui.png`, `m1_error.png`, `app/` |
| M2 Dockerfile, .dockerignore, YAML | `app/Dockerfile`, `app/.dockerignore`, `k8s/` |
| M2 `get all,pvc,configmap`, rs.status, init log | `results/m2_get_all.txt`, `m2_rs_status.txt`, `m2_mongo_init_log.txt` |
| M2 port-forward screenshot | `results/screenshots/m2_portforward.png` |
| M2 persistence proof | `results/m2_persistence.txt` |
| M3 Ingress + screenshot | `k8s/50-ingress.yaml`, `results/screenshots/m3_ingress.png` |
| M3 requests per pod (3 and 5 replicas) | `results/traffic_dist_r3.csv`, `traffic_dist_r5_early.csv`, `traffic_dist_r5.csv` |
| M3 self-healing | `results/m3_watch_webpod.txt`, `m3_watch_primary.txt`, `m3_primary_failover.txt`, `traffic_selfheal_*.csv` |
| M3 rollout | `results/m3_rollout_status.txt`, `m3_rollout_history.txt`, `traffic_rollout.csv` |
| M4 raw data | `results/run_r*_rate*/`, `results/httperf/`, `results/autoscale/`, `results/fixed_runs.csv` |
| M4 graphs | `results/graphs/graph1_response_time.png`, `graph2_pod_cpu.png`, `graph3_autoscaling.png` |
| Report | `Assignment4_Report.docx` -> PDF |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ImagePullBackOff` / `ErrImageNeverPull` on emi-web | image not in Minikube: repeat `minikube image load emi-web:1.0` (`minikube image ls`) |
| `kubectl apply` fails on the Ingress with a webhook error | ingress controller not ready yet: run the `kubectl wait ...` from step 2, apply again |
| `emi.local` does not open | `minikube tunnel` not running, or the `/etc/hosts` line is missing |
| `kubectl top` says metrics not available | wait 1-2 minutes after enabling metrics-server; check `kubectl get pods -n kube-system` |
| pods stay `Pending` | not enough CPU/RAM: raise Docker Desktop resources, then `minikube delete` and start again with step 2 |
| web pods stay `0/1` | check `kubectl logs job/mongo-init -n emi` and `kubectl get pods -n emi`; they wait for the replica set |
| mongo pods crash with `Illegal instruction` | the VM CPU lacks AVX, which MongoDB 5+ needs; unlikely on a 2016 Mac - if it happens you need a mongo:4.4 variant of the init Job |
| start over completely | `kubectl delete namespace emi` (also removes the PVCs), then `kubectl apply -f k8s/` |
| stop the cluster | `minikube stop` (keeps data) or `minikube delete` (removes everything) |
