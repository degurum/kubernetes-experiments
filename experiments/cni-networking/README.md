# CNI・Service・NetworkPolicyを手で追う実験

Pod IPへの通信、Serviceの宛先選択、readiness、NetworkPolicyをこの順で確かめます。各節の「予想」は判定の目安であり、実測値ではありません。[2026-10-25の実測と検証範囲](../../results/cni-networking-2026-10-25.md)は別に記録しています。実行結果は[記録用テンプレート](record-template.md)を構成ごとに複製して記入してください。

| 構成 | Pod転送 | Service実装 | 設定 |
| --- | --- | --- | --- |
| Calico | Calico Iptables・VXLAN | kube-proxy iptables | [kind-calico.yaml](kind-calico.yaml) |
| Cilium | Cilium eBPF・VXLAN | Cilium kube-proxy replacement | [kind-cilium.yaml](kind-cilium.yaml) |

まずCalicoを記録し、その後にCiliumを別のkindクラスタで記録します。構成以外の条件も変わり得るため、観測値を製品一般の性能順位には使いません。すでに実験用クラスタやサンプルが存在する場合は、作成・導入コマンドを繰り返さず、下の確認から始めます。

## 0. 主端末を準備する

**場所：** LinuxのBash。Windowsでは実験用WSLのBashを使い、以下をPowerShellへ直接貼り付けません。準備、DNS/Service、Policyはこの**主端末1つ**で実行します。readinessの既存TCPを流し続ける間だけ、同じWSLの**接続端末1つ**を追加します。ノードの規則を見る場合も主端末から `docker exec` でkindノードコンテナー内のコマンドを実行します。ノード用の別端末やSSHは不要です。

リポジトリのルートで実行します。新しいBashでは変数・関数を設定し直してください。Calicoなら `LAB_NAME=cni-calico` を使い、Ciliumでは `LAB_NAME=cni-cilium` に替えます。

```bash
test -f experiments/cni-networking/app.yaml
docker version
kind version
kubectl version --client
helm version
LAB_NAME=cni-calico
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
set -o pipefail
umask 077
mkdir -p "$HOME/cni-lab-observations"
SESSION_DIR=$(mktemp -d "$HOME/cni-lab-observations/${LAB_NAME}.XXXXXXXX")
printf 'context=%s\nSESSION_DIR=%s\n' "$LAB_CONTEXT" "$SESSION_DIR"
kind get clusters
```

`SESSION_DIR` はリポジトリ外の保存先です。観測のたびにその下へ `mktemp` で別のディレクトリを作ります。生ログ、kubeconfig、秘密値、個人の絶対パスを公開リポジトリに入れません。Bashで `set -e` を有効にすると、Policyで期待する拒否（終了コード1）でもシェルが終了するので、主端末全体では有効にしません。後述のsnapshotだけは丸括弧内で有効にし、途中の取得失敗を検出します。

端末全体を記録する `script` 等をすでに使っている場合、その記録シェルの中で再起動しません。端末ごとに異なる出力ファイルを使い、同じファイルに並行追記しないでください。この手順の `tee` は観測データの保存で、端末全体の記録とは別です。

**止める条件：** 指定した名前が別作業のクラスタに使われている、Pod/Service CIDRが利用中のネットワークと重なる、または `k` が意図しないcontextを指す場合。所有関係を確認するまで作成・変更しません。

## 1. クラスタとサンプルを確認する

**場所：主端末。** 既存の `cni-calico` または `cni-cilium` がある場合、下記の作成・CNI導入は**飛ばし**、基準状態の確認へ進みます。新規作成のときだけ対応する構成のコマンドを実行します。Calico native v3の導入では、kind設定の `MutatingAdmissionPolicy` と `admissionregistration.k8s.io/v1beta1` が必要です。`no matches for kind` 等が出たら導入を止め、設定を調べます。

```bash
# Calicoを新規作成するときだけ
kind create cluster --name cni-calico --config experiments/cni-networking/kind-calico.yaml
```

作成が成功したらCalicoを導入します。作成失敗時はここから先を実行しません。

```bash
k api-resources --api-group=admissionregistration.k8s.io
k create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/v3_projectcalico_org-v1beta1.yaml
k create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/tigera-operator.yaml
k wait --for=condition=Established crd/installations.operator.tigera.io --timeout=120s
k apply -f experiments/cni-networking/calico-installation.yaml
```

```bash
# Ciliumを新規作成するときだけ。先に LAB_NAME=cni-cilium として0節を実行する
kind create cluster --name cni-cilium --config experiments/cni-networking/kind-cilium.yaml
```

作成が成功したらCiliumを導入します。作成失敗時はここから先を実行しません。

```bash
CNI_API_IP=$(docker inspect --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}' cni-cilium-control-plane)
test -n "$CNI_API_IP"
helm install cilium oci://quay.io/cilium/charts/cilium --version 1.20.2 \
  --kube-context "$LAB_CONTEXT" --namespace kube-system \
  --values experiments/cni-networking/cilium-values.yaml \
  --set k8sServiceHost="$CNI_API_IP" --set k8sServicePort=6443
```

既存クラスタが停止中なら、先に今回の3ノードだけを再開する必要があります。5節の切り替え手順と `docker ps -a` で対象を確認してください。Ciliumを再開する場合は、control-planeの現在のコンテナーIPとHelmの `k8sServiceHost` も照合し、不一致ならそのまま進めず調べます。今回の実測は新規Cilium構築までで、停止後のCilium再起動は未検証です。

**基準状態の確認：** 1 control-planeと2 workerがReady、CoreDNSが稼働し、選んだCNIのPodが正常かを見ます。資材の固定値はKubernetes v1.35.0、Calico v3.33.0、Cilium 1.20.2、Pod MTU 1400です。Python 3.12.12とDNS用agnhost 2.39もimage digest付きで指定しています。Calicoはkube-proxyを使用し、Ciliumはこの構成では使用しません。実際の版、imageID、kernel、Corefile、MTU、Pod/Service CIDRを記録します。失敗したらサンプル適用前に止めます。

```bash
k config view --minify
k get --raw=/readyz
k wait --for=condition=Ready node --all --timeout=300s
k -n kube-system rollout status deployment/coredns --timeout=180s
k get nodes -o wide
k -n kube-system get daemonsets,deployments,pods -o wide
k -n kube-system get configmap coredns -o yaml
```

Calicoの場合だけ：

```bash
k -n tigera-operator rollout status deployment/tigera-operator --timeout=180s
k -n calico-system rollout status daemonset/calico-node --timeout=300s
k -n calico-system rollout status deployment/calico-kube-controllers --timeout=180s
k get tigerastatus
k -n kube-system get configmap kube-proxy -o yaml
```

Ciliumの場合だけ：

```bash
k -n kube-system wait --for=condition=Ready pod -l k8s-app=cilium --timeout=300s
k -n kube-system get pods -l k8s-app=cilium -o wide
k -n kube-system get daemonsets
helm get values cilium --kube-context "$LAB_CONTEXT" -n kube-system --all
```

**サンプル：** `cni-lab` Namespaceがすでにある場合は `app.kubernetes.io/managed-by=cni-lab-notes` を確認します。別用途のNamespaceなら止めます。既存サンプルがReadyなら適用を飛ばし、Policyがないことを確認します。サンプルが未作成のときだけ以下の適用を実行します。

```bash
k get namespace cni-lab --show-labels --ignore-not-found
```

Namespaceが存在するときだけ、現在の資材とPolicyを確認します：

```bash
k -n cni-lab get pods,service,networkpolicies
```

既存サンプルを再利用する前に、ConfigMap内のPythonとPod・probe・Service設定がこのcheckoutの資材に一致することも確認します。次の `diff` は変更を保存しません。差分なしの終了コード0なら再利用し、差分ありの1や取得エラーなら、内容を調べるまで上書きせず停止します。

```bash
k diff -f experiments/cni-networking/app.yaml
printf 'app diff rc=%s\n' "$?"
k -n cni-lab create configmap cni-lab-scripts \
  --from-file=experiments/cni-networking/server.py \
  --from-file=experiments/cni-networking/client.py \
  --dry-run=client -o yaml | k diff -f -
printf 'scripts diff rc=%s\n' "$?"
```

Namespaceとサンプルが未作成の場合だけ：

```bash
k apply -f experiments/cni-networking/namespace.yaml
k -n cni-lab create configmap cni-lab-scripts \
  --from-file=experiments/cni-networking/server.py \
  --from-file=experiments/cni-networking/client.py \
  --dry-run=client -o yaml | k apply -f -
k apply -f experiments/cni-networking/app.yaml
```

続けて実際の状態を確認します。backend 2 Pod、`client-allowed`、`client-denied`、`dns-probe` がReadyで、NetworkPolicyが空であることが開始条件です。別のPolicyがあればこの手順では削除せず、先へ進みません。

```bash
k -n cni-lab rollout status deployment/echo --timeout=180s
k -n cni-lab wait --for=condition=Ready pod --all --timeout=180s
k -n cni-lab get pods -o wide
k -n cni-lab get networkpolicies
k -n cni-lab get service echo -o yaml
k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
POD_IP=$(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[0].status.podIP}')
```

## 2. DNSとServiceを比べる

**目的・場所：** 主端末で、ポリシーのない状態のPod IP直接、ClusterIP、FQDNを比べます。`client.py` は結果をJSONで出します。成功は**終了コード0に加え、各JSONの `status: 200` と `error` がないこと**で判断します。HTTP非200でもPythonが終了コード0を返し得ます。通信エラーなら `error` が出て通常は終了コード1です。`kubectl exec` 自体のエラーやJSON出力なしは観測失敗として扱います。

```bash
DNS_DIR=$(mktemp -d "$SESSION_DIR/dns-service.XXXXXXXX")
k -n cni-lab exec client-allowed -- cat /etc/resolv.conf | tee -a "$DNS_DIR/resolv.log"
rc=$?
printf 'resolv.log rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
k -n cni-lab get pods -o wide | tee -a "$DNS_DIR/pod-placement.log"
rc=$?
printf 'pod-placement.log rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
for POD_IP in $(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[*].status.podIP}'); do
  k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 | tee -a "$DNS_DIR/pod-ip.jsonl"
  rc=$?
  printf 'pod-ip.jsonl rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
  k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 | tee -a "$DNS_DIR/denied-before-policy.jsonl"
  rc=$?
  printf 'denied-before-policy.jsonl rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
done
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --count 10 | tee -a "$DNS_DIR/service-ip.jsonl"
rc=$?
printf 'service-ip.jsonl rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
# resolv.conf と Corefile の実値を見て、必要なら cluster domain を直す
CLUSTER_DOMAIN=cluster.local
SERVICE_FQDN=echo.cni-lab.svc.$CLUSTER_DOMAIN.
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats | tee -a "$DNS_DIR/fqdn-udp.log"
rc=$?
printf 'fqdn-udp.log rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +tcp +stats | tee -a "$DNS_DIR/fqdn-tcp.log"
rc=$?
printf 'fqdn-tcp.log rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
k -n cni-lab exec dns-probe -- dig echo +search +showsearch | tee -a "$DNS_DIR/short-name.log"
rc=$?
printf 'short-name.log rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
k -n cni-lab exec client-allowed -- python -B /app/client.py echo --count 5 | tee -a "$DNS_DIR/short-name.jsonl"
rc=$?
printf 'short-name.jsonl rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_FQDN" --count 5 | tee -a "$DNS_DIR/fqdn.jsonl"
rc=$?
printf 'fqdn.jsonl rc=%s\n' "$rc" | tee -a "$DNS_DIR/exit-codes.log"
```

**予想：** 両clientから2つのPod IPへ接続できます。Pod一覧のNODE列とJSONの `backend.node` を対応させ、clientと同じworker・別のworkerの両経路を記録します。Serviceを使ったJSONには両backendが現れ得ます。FQDNのDNS回答はServiceのClusterIPです。短名はPodの `resolv.conf` のsearch設定に依存します。通信が期待と違えば、JSON、DNSの回答、Serviceと全EndpointSliceを保存し、次へ進まず原因を確認します。ここまでの必須判定はPod/Service/DNS/HTTPです。ノード規則、conntrack、DNS metricsの採取は後の補助観測です。

## 3. readinessと既存TCPを比べる

**目的：** 既存の1本のTCPを維持したまま、その接続先backendをNotReadyにします。主端末で正常・失敗・回復の各状態を保存し、有限回の**新規TCP**を各状態で実行します。追加する接続端末は既存TCP専用です。第三の端末は使いません。少なくとも別のbackend 1 PodをReadyのまま残します。

### 3-1. 接続端末を開く

同じWSLの新しいBashを開き、同じリポジトリルートで実行します。Ciliumなら1行目を変更してください。変数と関数は端末間で共有されないため、接続端末でもcontextとService IPを設定します。保存先は独立したディレクトリを作るので、主端末のパスを入力する必要はありません。

```bash
# 接続端末
LAB_NAME=cni-calico
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
set -o pipefail
umask 077
mkdir -p "$HOME/cni-lab-observations"
KEEP_DIR=$(mktemp -d "$HOME/cni-lab-observations/${LAB_NAME}.keepalive.XXXXXXXX")
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
printf 'KEEP_DIR=%s\n' "$KEEP_DIR"
k -n cni-lab exec -it client-allowed -- \
  python -B /app/client.py "$SERVICE_IP" --mode keepalive --count 1800 \
  2>"$KEEP_DIR/kubectl.stderr.log" | tee -a "$KEEP_DIR/keepalive.log"
  rc=$?
  printf 'keepalive.log rc=%s\n' "$rc" | tee -a "$KEEP_DIR/exit-codes.log"
```

接続端末は前面のままにし、別のコマンドを入力しません。`-it` はCtrl+CをPod内のPythonへ届けるために付けています。実際の対話Bashから実行し、TTYを使えないという警告が出たら先へ進みません。TTY内の標準出力と標準エラーは `keepalive.log` に合流し、手動終了時は `event: interrupted` と終了コード130が残ります。これは通信失敗とは区別します。最初のJSONにある `backend.pod`、`backend.uid`、`backend.connection_id` を読みます。表示がない、`error` がある、または `status` が200でなければ主端末の変更へ進みません。切断時は自動再接続しません。回数到達・Ctrl+C・通信エラーを記録で区別します。`&` によるバックグラウンド実行や補助スクリプトは不要です。

### 3-2. 主端末で対象を確定し、正常時を保存する

接続端末の初回JSONを見て、**主端末だけ**で `TARGET_POD` を入力します。表示されたPod名・UIDが初回JSONと一致し、ラベルが `app=echo` であることを確認します。`app=echo` は2 Podに共通するため、既存TCPが選んだ1 Podを指定できません。`kubectl exec` に `-l` はなく、`exec deployment/echo` でも同じ接続先を選ぶ保証はありません。全体観測にはラベル、変更対象にはこのPod名を使います。

```bash
# 主端末
read -r -p '既存TCPの初回JSONにある backend.pod: ' TARGET_POD
k -n cni-lab get pod "$TARGET_POD" \
  -o jsonpath='{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.labels.app}{"\t"}{.status.podIP}{"\n"}'
TARGET_IP=$(k -n cni-lab get pod "$TARGET_POD" -o jsonpath='{.status.podIP}')
START_UTC=$(date -u +%FT%TZ)
READY_DIR=$(mktemp -d "$SESSION_DIR/readiness.XXXXXXXX")
printf 'READY_DIR=%s\n' "$READY_DIR"
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$READY_DIR/normal-snapshot.log"
rc=$?
printf 'normal-snapshot.log rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --mode new --count 12 --interval 0.5 \
  2>&1 | tee -a "$READY_DIR/normal-new.jsonl"
  rc=$?
  printf 'normal-new.jsonl rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
```

**正常時の予想：** 両backendがReadyで、全EndpointSlice中の対応するendpointがreadyです。新規接続の応答は `status: 200`、`error` なしです。短い試行で両Podへ均等に割り振られることは成功条件ではありません。snapshotの終了コードが非0、接続端末が終了、または対象のUIDが変化したらここで止めます。

転送規則も比較する場合は、**ここで主端末から**[補助観測](#補助観測ノード規則mapconntrackwatch)の該当構成を採取します。正常時の記録を取ってから次へ進みます。

### 3-3. 対象だけをNotReadyにする

**場所：主端末。** `/state/not-ready` は対象Podの専用emptyDir内のflagです。既存TCPを接続端末で流したまま、対象だけのreadinessを落とします。`wait` はPodのReady=Falseを待つもので、全ノードの転送設定の反映完了を保証しません。

```bash
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").touch(); print(datetime.now(timezone.utc).isoformat(),"readiness-fail")' \
  2>&1 | tee -a "$READY_DIR/flag.log"
  rc=$?
  printf 'flag.log rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab wait --for=condition=Ready=false "pod/$TARGET_POD" --timeout=120s
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$READY_DIR/not-ready-snapshot.log"
rc=$?
printf 'not-ready-snapshot.log rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
```

対象のEndpointSlice条件がまだ正常時のままなら、上のsnapshot部分を最大3回、2秒ほど間を置いて取り直します。`tee -a` は同じファイルへ追記します。条件が変わらない、または取得エラーなら新規TCPの判定へ進まず、状態を記録して回復します。反映を確認したら次を実行します。

```bash
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --mode new --count 12 --interval 0.5 \
  2>&1 | tee -a "$READY_DIR/not-ready-new.jsonl"
  rc=$?
  printf 'not-ready-new.jsonl rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /whoami --count 2 \
  2>&1 | tee -a "$READY_DIR/not-ready-direct.jsonl"
  rc=$?
  printf 'not-ready-direct.jsonl rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /readyz --count 1 \
  2>&1 | tee -a "$READY_DIR/not-ready-readyz.jsonl"
  rc=$?
  printf 'not-ready-readyz.jsonl rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
```

**失敗中の予想：** 対象はReady=False、もう一方はReady=True。EndpointSliceではPod名・IP・targetRefと `conditions.ready/serving/terminating` を対応させます。対象IPがSliceから消えるとは限りません。安定後の新規Service接続では対象が選ばれず、DNS回答は引き続きClusterIPと予想します。一方、対象Pod IPへ直接つなぐ `/whoami` は200、`/readyz` は503を返す設計です。`/readyz` が503でもclient.pyの終了コードは0になり得るため、JSONの `status` を見ます。既存TCPが継続するか停止するかは**実測**として記録し、どちらも成功条件にしません。想定外の応答、JSON `error`、接続端末の停止があれば時刻と出力を保存し、回復手順へ進みます。

転送規則も比較する場合は、**回復させる前に主端末で**同じノード・agentの[補助観測](#補助観測ノード規則mapconntrackwatch)を採取します。

### 3-4. 回復して終了する

**場所：主端末。** 失敗中の観測が終わったら、次へ進む前にflagを消します。途中で別のコマンドが失敗しても、`TARGET_POD` が残っていればこの回復を実行します。

```bash
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").unlink(missing_ok=True); print(datetime.now(timezone.utc).isoformat(),"readiness-recover")' \
  2>&1 | tee -a "$READY_DIR/recover.log"
  rc=$?
  printf 'recover.log rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab wait --for=condition=Ready "pod/$TARGET_POD" --timeout=120s
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$READY_DIR/recovered-snapshot.log"
rc=$?
printf 'recovered-snapshot.log rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
```

両backendのEndpointSlice条件もreadyへ戻ったことを確認します。遅れている場合は上のsnapshot部分を最大3回、2秒ほど間を置いて取り直し、なお戻らなければ新規TCPの判定を止めて状態を記録します。戻ったら次を実行します。

```bash
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --mode new --count 12 --interval 0.5 \
  2>&1 | tee -a "$READY_DIR/recovered-new.jsonl"
  rc=$?
  printf 'recovered-new.jsonl rc=%s\n' "$rc" | tee -a "$READY_DIR/exit-codes.log"
k -n cni-lab wait --for=condition=Ready pod -l app=echo --timeout=120s
k -n cni-lab get pods -l app=echo -o wide
POD_IP=$(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[0].status.podIP}')
```

**回復後の予想：** backendは2→1→2 Podが利用可能になり、通常ServiceのDNS回答はClusterIPのままです。Pod名・UID・IP・restartCountが途中で変わった場合は、同一Podのreadinessだけを変えた比較として扱いません。接続端末では観測後にCtrl+Cで終了し、終了理由を記録します。再実行時は、旧対象を回復して両backendのReadyを確認し、主端末で新しい `READY_DIR`、接続端末で新しい `KEEP_DIR` を `mktemp` で作り、3-1からやり直します。旧ログは残します。

snapshotは順次取得したAPI状態で、同一時刻の原子的な全体像ではありません。probe行は再取得したsnapshotに重複掲載され得ます。正確な伝播遅延はこの基本手順から算出しません。

転送規則も比較する場合は、回復後にも主端末で同じノード・agentの[補助観測](#補助観測ノード規則mapconntrackwatch)を採取します。

## 4. NetworkPolicyの両方向を確かめる

**場所：主端末。** backend 2 PodがReadyで、既存TCP観測が終了した状態から始めます。ここではPod IPへの**新規接続**を使い、DNS失敗とPolicy拒否を混ぜません。`POLICY_DIR` を観測単位で作り、各段階のJSONとコマンドエラーを残します。Policyの反映は非同期なので、適用直後に期待と違えば状態と時刻を保存し、同じ通信を最大3回、2秒ほど間を置いて取り直します。なお期待と違えば次のPolicyを適用せず、状態を確認します。

```bash
POLICY_DIR=$(mktemp -d "$SESSION_DIR/policy.XXXXXXXX")
k -n cni-lab wait --for=condition=Ready pod -l app=echo --timeout=120s
k -n cni-lab get networkpolicies
k -n cni-lab get pods -o wide | tee -a "$POLICY_DIR/pod-placement.log"
rc=$?
printf 'pod-placement.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
POD_IP=$(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[0].status.podIP}')
```

以下は2 backendそれぞれに新しい接続を作り、各段階の出力と終了コードを保存します。出力直後の `rc=$?` を別のコマンドで挟まないでください。**許可**はJSONの `status: 200`・`error` なし・終了コード0、**拒否**はJSONの `error` と終了コード1を目安にします。JSONが出ない、`kubectl exec` のエラー、Pod消失などは期待した拒否ではなく実行エラーです。HTTP非200は終了コード0でも許可成功ではありません。

1. **Ingress隔離を適用。予想：allowed/deniedとも拒否。**

   ```bash
   k apply -f experiments/cni-networking/policy-server-isolate.yaml
   for POD_IP in $(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[*].status.podIP}'); do
     k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/server-isolate-allowed.log"
     rc=$?
     printf 'server-isolate-allowed.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
     k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/server-isolate-denied.log"
     rc=$?
     printf 'server-isolate-denied.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   done
   ```

2. **allowedだけをIngress許可。予想：allowedは200、deniedは拒否。** 空の隔離Policyは許可Policyを打ち消さず、同じ方向の許可は和集合です。

   ```bash
   k apply -f experiments/cni-networking/policy-server-allow.yaml
   for POD_IP in $(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[*].status.podIP}'); do
     k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/server-allow-allowed.log"
     rc=$?
     printf 'server-allow-allowed.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
     k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/server-allow-denied.log"
     rc=$?
     printf 'server-allow-denied.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   done
   ```

3. **`role=client` のEgressを隔離。予想：allowedも拒否。** このラベルは `client-allowed`、`client-denied`、`dns-probe` に付いています。宛先Ingressの許可だけでは送信元Egressは開きません。

   ```bash
   k apply -f experiments/cni-networking/policy-client-isolate.yaml
   for POD_IP in $(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[*].status.podIP}'); do
     k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/client-isolate-allowed.log"
     rc=$?
     printf 'client-isolate-allowed.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
     k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/client-isolate-denied.log"
     rc=$?
     printf 'client-isolate-denied.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   done
   ```

4. **backend TCP 8080へのEgressを許可。予想：allowedは200、deniedは拒否。**

   ```bash
   k apply -f experiments/cni-networking/policy-client-allow.yaml
   for POD_IP in $(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[*].status.podIP}'); do
     k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/client-allow-allowed.log"
     rc=$?
     printf 'client-allow-allowed.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
     k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/client-allow-denied.log"
     rc=$?
     printf 'client-allow-denied.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   done
   ```

5. **Ingress許可の和集合を確認。** `client-isolate` だけを外したあと、deniedの拒否→ `server-allow-all` 追加後の200→削除後の拒否を確認します。その後 `client-isolate` を戻しても、allowedは `client-allow` で200の予想です。

   ```bash
   k -n cni-lab delete networkpolicy client-isolate
   k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/allow-all-before.log"
   rc=$?
   printf 'allow-all-before.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k apply -f experiments/cni-networking/policy-server-allow-all.yaml
   k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/allow-all-during.log"
   rc=$?
   printf 'allow-all-during.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k -n cni-lab delete networkpolicy server-allow-all
   k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/allow-all-after.log"
   rc=$?
   printf 'allow-all-after.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k apply -f experiments/cni-networking/policy-client-isolate.yaml
   k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2 2>&1 | tee -a "$POLICY_DIR/isolate-restored.log"
   rc=$?
   printf 'isolate-restored.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   ```

6. **DNSのEgressを別に許可。** 先にCoreDNS Podのラベルを確認します。DNS許可前の `dig` は失敗、追加後のUDP/TCP 53は成功し、既存のTCP 8080許可も維持される予想です。`dig` の終了コードだけでなく、回答のstatus/ANSWERも確認します。

   ```bash
   k -n kube-system get pods -l k8s-app=kube-dns -o wide
   k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +time=1 +tries=1 2>&1 | tee -a "$POLICY_DIR/dns-before.log"
   rc=$?
   printf 'dns-before.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k apply -f experiments/cni-networking/policy-client-dns.yaml
   k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats 2>&1 | tee -a "$POLICY_DIR/dns-udp.log"
   rc=$?
   printf 'dns-udp.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +tcp +stats 2>&1 | tee -a "$POLICY_DIR/dns-tcp.log"
   rc=$?
   printf 'dns-tcp.log rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_FQDN" --count 2 2>&1 | tee -a "$POLICY_DIR/dns-http.jsonl"
   rc=$?
   printf 'dns-http.jsonl rc=%s\n' "$rc" | tee -a "$POLICY_DIR/exit-codes.log"
   k -n cni-lab get networkpolicies
   ```

期待と異なる場合はAPI上のPolicyとPodラベル、clientのJSON/エラーを保存して止めます。別の名前・NamespaceのPolicyを一括削除しません。

## 補助観測：ノード規則、map、conntrack、watch

基本の必須判定は上のPod/EndpointSlice/DNS/HTTPです。以下は原因や反映差を調べる場合だけ、**主端末**で正常・失敗・回復の各時点に同じ対象を読み取ります。`NODE` はkindノードコンテナー名です。`docker exec` の右側だけがノード内で動き、ホスト設定は変更しません。ツールがない場合は未採取と記録します。

```bash
# Calico: clientが動くノードを選ぶ。必要なら別のbackendノードでも同じ手順
NODE=$(k -n cni-lab get pod client-allowed -o jsonpath='{.spec.nodeName}')
docker exec "$NODE" iptables --version
docker exec "$NODE" iptables-save -t nat
docker exec "$NODE" iptables-save -t filter
docker exec "$NODE" conntrack -L -p tcp
```

Ciliumではagent Podとそのノードを `-o wide` で対応付けてから選びます。mapにbackend IPが残ることだけで新規選択対象とは判定しません。socket-LBではパケット生成前に宛先が選ばれる場合があります。

```bash
k -n kube-system get pods -l k8s-app=cilium -o wide
read -r -p '観測するCilium agent Pod名: ' CILIUM_POD
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg status --verbose
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg service list
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg bpf lb list
```

遷移時刻を詳しく調べる `kubectl get pods/endpointslices --watch` は追加観測です。基本のsnapshotから正確な全段階の伝播遅延は出せません。watchを使う場合は別の観測として保存し、resourceVersionと受信UTC時刻を記録してください。

## 5. 記録・片付け・構成の切り替え

構成ごとに[記録用テンプレート](record-template.md)へ予想・実測・解釈を分けて記入します。公開するのは整理した結果と必要な抜粋だけです。未確認もそのまま残します。実測の記録は `results/` に置き、生ログ、pcap、kubeconfig、秘密値、不要な識別子、個人の絶対パスは公開しません。

終了前に主端末で対象context、Namespaceの所有ラベル、PodのReady、残ったPolicyを確認します。開始時にPolicyが空で、この節の6個を自分で作成した場合だけ、名前を指定して片付けます。途中終了時も対象を確認してから実行してください。

```bash
k get namespace cni-lab --show-labels
k -n cni-lab get networkpolicies
k -n cni-lab delete networkpolicy server-isolate server-allow server-allow-all client-isolate client-allow client-dns --ignore-not-found
k -n cni-lab wait --for=condition=Ready pod --all --timeout=120s
```

readinessのflagは3-4で回復済みであることを確認します。NetworkPolicyが空なら、このサンプルを残して次回は1節の基準状態から再開できます。Namespace、リポジトリ、記録を削除する必要はありません。

```bash
k -n cni-lab get pods,service,networkpolicies
k get namespace cni-lab --show-labels
kind get clusters
```

Calicoの記録後、Ciliumに切り替えるときは0節から `LAB_NAME=cni-cilium` で開始し、存在しないクラスタだけを作ります。同時起動の資源が足りなければ、完了した実験用クラスタのノードを停止して順番に実行できます。まず `kind get nodes --name cni-calico` で次の3個が今回の対象であることを確認します。停止は通信を中断しますが、ノードコンテナーとクラスタのデータは保持します。

```bash
# Calicoの実験・記録が完了した場合だけ、主端末で実行
kind get nodes --name cni-calico
docker stop cni-calico-control-plane cni-calico-worker cni-calico-worker2
```

Ciliumの記録後にCalicoへ戻す場合も、Cilium側で上のPolicy片付けとreadiness回復を済ませてから切り替えます。

```bash
kind get nodes --name cni-cilium
docker stop cni-cilium-control-plane cni-cilium-worker cni-cilium-worker2
docker start cni-calico-control-plane cni-calico-worker cni-calico-worker2
```

その後0節でCalicoのcontextを設定し直し、1節で3ノード・CNI・CoreDNS・サンプルのReadyを確認します。再起動前後の通信を1本の既存TCPの観測としてつなげません。

kind作成中に `could not find a log line that matches ... Multi-User System ...` が出た場合、CNIの導入はまだ始まっていないことがあります。保持されている失敗ノードの `docker logs` で最初のエラーを読みます。今回の[実測記録](../../results/cni-networking-2026-10-25.md)では、複数クラスタの同時起動時に `Failed to create control group inotify object: Too many open files` を確認しました。ホストの上限変更をこの手順で自動実行しません。作成に失敗したノードが残っている場合は、先にログと所有関係を確認し、同名への `create` / `install` を重ねないでください。原因の参考は[kindの既知の問題](https://kind.sigs.k8s.io/docs/user/known-issues/#pod-errors-due-to-too-many-open-files)です。

参考：[kind](https://kind.sigs.k8s.io/docs/user/configuration/)、[Kubernetes Service](https://kubernetes.io/docs/concepts/services-networking/service/)、[readiness probe](https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/)、[EndpointSlice](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)、[NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)。
