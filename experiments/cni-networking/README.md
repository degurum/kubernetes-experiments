# CNI・Service・NetworkPolicyを手で追う実験

Pod IPへの通信、Serviceのbackend選択、NetworkPolicyの許可を順に観測します。Serviceの基礎を理解している読者向けに、各コマンドを手で実行する手順です。ここにある期待値は実測結果ではありません。環境構築・CNI比較の実行結果は、空欄の[記録用テンプレート](record-template.md)へ記入します。

比較する構成は次の二つです。Aの実験を終えて記録・片付けをした後、Bを新しいクラスタで実行します。製品と構成の両方が変わるため、性能差を製品一般の順位へ広げません。

| 構成 | Pod転送 | Service実装 | kind設定 |
| --- | --- | --- | --- |
| A | Calico Iptables・VXLAN | kube-proxy iptables | [kind-calico.yaml](kind-calico.yaml) |
| B | Cilium eBPF・VXLAN | Cilium replacement、kube-proxyなし | [kind-cilium.yaml](kind-cilium.yaml) |

## 実行前の準備

LinuxのBash（WindowsではWSL2内のLinux）と、動作するDocker Engine、kind、kubectl、Helmを用意します。HelmはBで使います。kubectlはKubernetes v1.35に対応する版を使い、kindは固定したnode imageに対応する版を確認します。[kindの要件](https://kind.sigs.k8s.io/docs/user/quick-start/)、[Ciliumの要件](https://docs.cilium.io/en/stable/operations/system_requirements/)

追加のCilium CLI、Containerlab、クラウド環境は不要です。PythonスクリプトとDNS用digはPod image内のものを使います。iptables・conntrackはkindノード内にあれば読む補助観測で、なければ未採取と記録します。ローカルにPythonやdigを追加する必要はありません。

リポジトリのルートから実行します。以下はツールの確認のみです。

```bash
pwd
test -f experiments/cni-networking/app.yaml
docker version
kind version
kubectl version --client
helm version
```

進む順序は「Aの準備 → サンプル → DNS/転送 → readiness → Policy → 記録/片付け → Bで同じ手順」です。readinessの基本手順では、操作・既存TCP・新規TCPの3つのターミナルを使い分けます。watchによる遷移観測は追加手順です。各ターミナルでリポジトリのルートへ移動し、次の設定を入れてください。Bのときだけ`LAB_NAME=cni-cilium`へ替えます。k関数は毎回contextを指定するため、現在のkubectl contextを変更しません。

```bash
LAB_NAME=cni-calico
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
```

## 共通条件

kind単体で1 control-plane＋2 workerを、構成ごとに再作成します。同じLinux環境、kind版、kernel、Kubernetes patch、IPv4、MTU、VXLAN経路で比較します。[kind設定](https://kind.sigs.k8s.io/docs/user/configuration/)

準備した固定値はKubernetes v1.35.0、Calico v3.33.0、Cilium 1.20.2、Pod MTU 1400です。kindノード、Python 3.12.12、DNS観測用agnhost 2.39はタグとdigestを固定しています。互換性とデプロイは実行時に確認し、[記録用テンプレート](record-template.md)に残します。

サンプルは `cni-lab` Namespaceに限定します。各workerにbackendを1Pod配置し、初期状態にNetworkPolicyはありません。アプリPodは非root、read-only root filesystem、capabilitiesを全削除、ServiceAccount tokenをmountしません。readinessの切替は専用emptyDirのflagだけを変更します。CNIの導入とノードの観測にはラボの管理権限が必要です。

以下のコマンドはLinuxのシェル用です。実行前に、クラスタ名が既存の別作業と重ならないことと、Pod/Service CIDRが利用するネットワークと重ならないことを確認します。コマンドの例と期待する挙動を、実際に得た結果と分けて記録してください。

## クラスタの準備

### A：Calicoとkube-proxy

Aで実験するときに実行します。準備後は「基準状態」へ進み、Bの導入は飛ばします。

Calico v3.33.0のnative v3 manifestは`MutatingAdmissionPolicy`と`MutatingAdmissionPolicyBinding`を使います。Kubernetes v1.35では既定で無効なため、`kind-calico.yaml`でfeature gateと`admissionregistration.k8s.io/v1beta1`の両方を有効にしています。作成後のAPI一覧に両方が`v1beta1`で現れることを確認します。`no matches for kind`で失敗した場合は先へ進まず、設定を確認し、この実験用クラスタだけ作り直してください。[Kubernetes v1.35の必要設定](https://v1-35.docs.kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/)、[kindの設定](https://kind.sigs.k8s.io/docs/user/configuration/#feature-gates)

```bash
kind get clusters
kind create cluster --name cni-calico --config experiments/cni-networking/kind-calico.yaml
LAB_NAME=cni-calico
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
k config view --minify
k api-resources --api-group=admissionregistration.k8s.io
k create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/v3_projectcalico_org-v1beta1.yaml
k create -f https://raw.githubusercontent.com/projectcalico/calico/v3.33.0/manifests/tigera-operator.yaml
k wait --for=condition=Established crd/installations.operator.tigera.io --timeout=120s
k apply -f experiments/cni-networking/calico-installation.yaml
```

Calicoを明示的にIptables、VXLAN、MTU 1400へ設定します。kube-proxyもiptablesモードです。標準NetworkPolicyの観測から始め、任意のflow logs導入は基本の実験と分けます。[Calicoのkind手順](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind)、[Operator API](https://docs.tigera.io/calico/latest/reference/installation/api)

### B：Ciliumのreplacement

Bで実験するときに実行します。Aのクラスタを片付け、Aと同じホスト・ツールで新規に作成します。

```bash
kind get clusters
kind create cluster --name cni-cilium --config experiments/cni-networking/kind-cilium.yaml
LAB_NAME=cni-cilium
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
k config view --minify
CNI_API_IP=$(docker inspect --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}' cni-cilium-control-plane)
test -n "$CNI_API_IP"
helm install cilium oci://quay.io/cilium/charts/cilium --version 1.20.2 \
  --kube-context "$LAB_CONTEXT" --namespace kube-system \
  --values experiments/cni-networking/cilium-values.yaml \
  --set k8sServiceHost="$CNI_API_IP" --set k8sServicePort=6443
```

この例はDocker Engineの既定kind networkを使います。kube-proxyを作らず、CiliumがService経由に依存せずAPIへ接続する設定です。socket-LBを初回の観測対象に含め、途中でreplacementやsocket-LBを切り替えません。[Ciliumのkind手順](https://docs.cilium.io/en/stable/installation/kind/)、[replacementの設定](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/)

### 基準状態

```bash
k wait --for=condition=Ready node --all --timeout=300s
k -n kube-system rollout status deployment/coredns --timeout=180s
k get nodes -o wide
k get nodes -o yaml
k -n kube-system get daemonsets,deployments,pods -o wide
k -n kube-system get configmap coredns -o yaml
k -n kube-system get deployment coredns -o yaml
kind version
docker version
```

Aでは次を読みます。

```bash
k -n kube-system get configmap kube-proxy -o yaml
k -n kube-system get daemonset kube-proxy -o yaml
k get installation.operator.tigera.io default -o yaml
k get felixconfigurations.projectcalico.org -o yaml
k -n tigera-operator rollout status deployment/tigera-operator --timeout=180s
k -n calico-system rollout status daemonset/calico-node --timeout=300s
k -n calico-system rollout status deployment/calico-kube-controllers --timeout=180s
k get tigerastatus
k -n calico-system get pods -o wide
```

Bでは次を読みます。kube-proxyのDaemonSetがないことを確認します。

```bash
helm get values cilium --kube-context "$LAB_CONTEXT" -n kube-system --all
k -n kube-system get daemonsets
k -n kube-system get pods -l k8s-app=cilium -o wide
```

両構成で実際のnode imageID、kernel、CNI image、CoreDNS image・replicas・Corefileも残します。iptablesのlegacy/nftバックエンドもバージョン表示で確認します。

## サンプルの準備

まずNamespaceの有無を読みます。存在する場合は`app.kubernetes.io/managed-by=cni-lab-notes`のラボ用Namespaceか確認し、別用途ならここで止めます。既存ラボを再利用する場合は古いNetworkPolicyが残っていないことも確認します。

```bash
k get namespace cni-lab --show-labels --ignore-not-found
k -n cni-lab get networkpolicies
```

Namespaceがない状態での`get networkpolicies`のNotFoundは想定内です。

```bash
k apply -f experiments/cni-networking/namespace.yaml
k -n cni-lab create configmap cni-lab-scripts \
  --from-file=experiments/cni-networking/server.py --from-file=experiments/cni-networking/client.py \
  --dry-run=client -o yaml | k apply -f -
k apply -f experiments/cni-networking/app.yaml
k -n cni-lab rollout status deployment/echo --timeout=180s
k -n cni-lab wait --for=condition=Ready pod --all --timeout=180s
k -n cni-lab get pods -o wide
k -n cni-lab get networkpolicies
k -n cni-lab get service echo -o yaml
k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
POD_IP=$(k -n cni-lab get pods -l app=echo -o jsonpath='{.items[0].status.podIP}')
```

backendは `/whoami` と `/livez` が常時200、`/readyz` がflag設定中だけ503を返します。livenessProbeは付けず、readinessと再起動を分離します。readinessはperiod 2秒、timeout 1秒、failureThreshold 2、successThreshold 1ですが、Readyまでの反映を必ず4秒とは扱いません。Pod UID・IP・restartCountを状態ごとに記録します。

Serviceは通常のClusterIP、`publishNotReadyAddresses: false`、`sessionAffinity: None`、`internalTrafficPolicy: Cluster`です。少なくとも一つのbackendを常にReadyに保ちます。

## 1：DNSとService転送

最初はポリシーなしで、Pod IP直接・ClusterIP直接・Service名での新規TCP接続を比較します。HTTP応答のPod名とconnection IDを、APIと転送状態へ対応させます。

```bash
k -n cni-lab exec client-allowed -- cat /etc/resolv.conf
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 5
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --count 10
# resolv.confとCorefileから実際のcluster domainを確認して設定する
CLUSTER_DOMAIN=cluster.local
SERVICE_FQDN=echo.cni-lab.svc.$CLUSTER_DOMAIN.
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +tcp +stats
k -n cni-lab exec dns-probe -- dig echo +search +showsearch
k -n cni-lab exec client-allowed -- python -c 'import socket; print(socket.getaddrinfo("echo",8080,type=socket.SOCK_STREAM))'
k -n cni-lab exec client-allowed -- python -B /app/client.py echo --count 5
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_FQDN" --count 5
```

末尾dot付きFQDNのDNS応答はClusterIP、backend選択はその後のService実装です。短名のアプリresolverはsearch/ndotsに従うため、既定でsearchを使わないdigと同一視せず、digにも明示的に `+search +showsearch` を付けます。DNS問い合わせ自身もkube-dns Serviceへの通信を使います。

CoreDNSのkubernetes pluginはAPIを監視し、cache pluginはDNS回答をcacheします。Corefileのttl/cache設定を記録し、plugin標準5秒を全環境の値にしません。cache 30を「必ずTTLを30秒へ延長する」とも扱いません。複数CoreDNS Podのcacheは別なので、TTLの単調減少を成功条件にしません。全queryログは追加せず、既存の各PodのDNS/cache metricsが利用できれば差分を補助資料にします。[DNS仕様](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)、[kubernetes plugin](https://coredns.io/plugins/kubernetes/)、[cache plugin](https://coredns.io/plugins/cache/)

### 転送状態の観測

Aだけで実行します。各ノードを読むには、`$LAB_NAME-worker`を`$LAB_NAME-worker2`、`$LAB_NAME-control-plane`へ替えて繰り返します。ホスト側のiptablesは使いません。

```bash
# A：kindノード内の規則とconntrackを読む
docker exec "$LAB_NAME-worker" iptables --version
docker exec "$LAB_NAME-worker" iptables-save -t nat
docker exec "$LAB_NAME-worker" iptables-save -t filter
docker exec "$LAB_NAME-worker" conntrack -L -p tcp
```

Bだけで実行します。clientのノードと対象backendのノードにあるagentをそれぞれ指定します。

```bash
# B：ノードごとのagent Pod名を確認して指定する
k -n kube-system get pods -l k8s-app=cilium -o wide
read -r -p '観測するCilium agent Pod名: ' CILIUM_POD
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg status --verbose
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg service list
k -n kube-system exec "$CILIUM_POD" -- cilium-dbg bpf lb list
```

AではKUBE-SVC/KUBE-SEP・DNAT・conntrack、Bではfrontendとbackendの関連・状態を照合します。全ノードへ同時に反映されるとは扱いません。client/host/serverのpacket採取には、用意したラボノードでtcpdump等が利用できることと、対象のPod名前空間・インターフェースを確認することが必要です。アプリへ追加capabilityは付けません。採取できない観測点は未確認として記録します。

Ciliumのsocket-LBではpacket生成前に選択されることがあるため、packet中に元のVIPが見えないことだけで異常と判断しません。対応するHubbleや `cilium-dbg monitor -v -t trace-sock` のイベントを補助にします。アプリの `getpeername` による値も、実際のwire上の宛先と同一とは限りません。

## 2：readinessから新規接続先まで

ここではbackendを1 PodだけNotReadyにし、もう1 PodはReadyのまま残します。通常ServiceのDNS回答、既存のTCP接続、新しいTCP接続の接続先を分けて確認します。

### ターミナルの役割

LinuxのBashを3つ開き、次の名前で使い分けます。Windowsでは、いずれも同じ実験用WSLに入ったBashです。以下のBashコマンドをPowerShellへ直接貼り付けないでください。各Bashで、同じ実験クラスターのkubeconfigを使える状態にします。

| ターミナル | 実行すること | 実験中の状態 |
| --- | --- | --- |
| A：操作・状態確認 | 対象の特定、readiness変更、Pod・EndpointSlice・probeログ・DNS・転送状態の確認 | コマンドを順に実行する |
| B：既存TCP | 1本のHTTP/1.1接続を維持する | client.pyを前面で実行したままにする |
| C：新規TCP | 毎回新しいTCP接続を作る | client.pyを前面で実行したままにする |

BとCでは、通信を動かしている途中に別のコマンドを入力しません。変更や状態確認はAで行います。バックグラウンド実行の`&`は使いません。

**A・B・Cの各ターミナル**で、リポジトリのルートへ移動し、以下をそれぞれ実行します。構成BのCiliumを試している場合だけ、3つとも`LAB_NAME=cni-cilium`にします。「構成A/B」と「ターミナルA/B/C」は別の区分です。

```bash
# A・B・Cの各Bashで実行
LAB_NAME=cni-calico
LAB_CONTEXT=kind-$LAB_NAME
k() { kubectl --context "$LAB_CONTEXT" "$@"; }
set -o pipefail
umask 077
mkdir -p "$HOME/cni-lab-observations"
OBS_DIR=$(mktemp -d "$HOME/cni-lab-observations/readiness.XXXXXXXX")
printf 'context=%s\nこのターミナルの保存先=%s\n' "$LAB_CONTEXT" "$OBS_DIR"
k config view --minify
k get namespace cni-lab --show-labels
```

変数と関数はターミナル間で共有されません。Aで設定した`TARGET_POD`などをBやCへ入力し直す必要はありません。B・CはService IPをそれぞれ取得します。`OBS_DIR`も各ターミナルで別のディレクトリを作るため、同じファイルへ並行して書き込みません。役割A/B/Cと保存先を対応付けて控えてください。生ログはリポジトリ外に保存し、そのままcommitしません。

端末全体を`script`などで記録する場合も、**ターミナルごとに異なる出力ファイル**を使います。同じファイルへの追記や、既存の記録シェル内での二重起動は避けます。この節の`tee`は観測データ用で、端末全体の記録とは別です。Bashのコマンド履歴は設定によって終了時に共通ファイルへ追記されるため、端末別の観測記録としては使いません。

### A：開始前の状態を確認する

```bash
# ターミナルA
k -n cni-lab get pods -l app=echo -o wide
k -n cni-lab wait --for=condition=Ready pod -l app=echo --timeout=120s
k -n cni-lab get networkpolicies
k -n cni-lab get service echo -o yaml
```

backendが2 PodともReadyで、NetworkPolicyがないことを確認します。そうでなければ先へ進まず、サンプルの準備状態へ戻してください。実験中はDeploymentの更新・scale・Podの削除を行いません。

### B：既存TCPを開始する

```bash
# ターミナルB
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
k -n cni-lab exec client-allowed -- \
  python -B /app/client.py "$SERVICE_IP" --mode keepalive --count 1800 \
  2>"$OBS_DIR/keepalive.stderr.log" | tee "$OBS_DIR/keepalive.jsonl"
```

最初のJSONの`backend.pod`と`backend.uid`を見ます。この**既存接続が実際に選んだPod**だけを後でNotReadyにします。Bはそのまま動かし続けます。出力がない、JSONに`error`がある、またはHTTPが200でない場合はAの操作へ進まず、JSONと`keepalive.stderr.log`を確認します。

client.pyはPod名・UID・connection IDが同じか確認し、切断時は自動再接続せず停止します。`--count 1800`は手打ち時間を確保するための有限回数です。回数到達による正常終了、手動のCtrl+C、通信エラーによる停止を区別してください。終了した後に再実行した接続は、元の既存TCPとは別の観測です。

### A：Bの接続先を1回だけ指定する

```bash
# ターミナルAだけで実行。Bの初回JSONから入力する
read -r -p 'Bの初回JSONにある backend.pod: ' TARGET_POD
k -n cni-lab get pod "$TARGET_POD" \
  -o jsonpath='{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.labels.app}{"\t"}{.status.podIP}{"\n"}'
```

表示されたPod名・UIDがBの初回JSONと一致し、appラベルが`echo`であることを確認してから進みます。`client-allowed`は通信を送るPodで、変更対象のbackendではありません。違うPodを選ぶと、既存TCPとreadiness変更の関係を比較できません。

```bash
# ターミナルA
TARGET_IP=$(k -n cni-lab get pod "$TARGET_POD" -o jsonpath='{.status.podIP}')
START_UTC=$(date -u +%FT%TZ)
# 「1：DNSとService転送」で確認した実際のcluster domainを使う
CLUSTER_DOMAIN=cluster.local
SERVICE_FQDN=echo.cni-lab.svc.$CLUSTER_DOMAIN.
```

`app=echo`は全backendの取得には使えますが、このDeploymentの2 Podに共通するラベルです。**Bの接続先1 Podを特定する指定にはなりません。** `kubectl exec`には`-l`がなく、`exec deployment/echo`もBの接続先と同じPodを選ぶ保証はありません。この手順では`TARGET_POD`をAだけに保持し、全体の確認にはラベルselectorを使います。

専用ラベルを1 Podに付ける方法でも、Bの接続先との最初の対応付けと「一致が1 Podだけ」の確認は必要です。同じDeploymentのPod templateにラベルを書くと両方に付きます。Serviceのselectorや既存の`app`ラベルを変えてbackendを外す操作は、ここでは行いません。

### C：新規TCPを開始する

```bash
# ターミナルC
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
k -n cni-lab exec client-allowed -- \
  python -B /app/client.py "$SERVICE_IP" --mode new --count 1800 \
  2>"$OBS_DIR/new.stderr.log" | tee "$OBS_DIR/new.jsonl"
```

Cは各リクエストで新しいTCP接続を作ります。`backend.pod`を見て、最初は両backendが選ばれることを確認します。短い区間で均等に分散するとは限りません。Cも動かしたままにしてAへ戻ります。

Cだけを再実行する場合は、Cで次のコマンドを実行して新しい保存先を作り、通信コマンドを実行します。以前のログを上書きせず、別の新規接続観測として記録します。

Bが終了して観測し直す場合は、まずCをCtrl+Cで止めます。AでNotReadyに変更済みなら、以前の`TARGET_POD`を保持したまま「A：readinessを回復させる」の手順で回復させます。両backendがReadyであることを確認してから、**A・B・Cすべてで**新しい`OBS_DIR`を作ります。その後「B：既存TCPを開始する」から順にやり直し、Aの`TARGET_POD`と`START_UTC`も新しい観測の値に設定し直してください。

```bash
# Cだけを再実行するならCで、Bから観測し直すならA・B・Cで実行
OBS_DIR=$(mktemp -d "$HOME/cni-lab-observations/readiness.XXXXXXXX")
printf '新しい観測の保存先=%s\n' "$OBS_DIR"
```

### A：正常時の状態を保存する

```bash
# ターミナルA。コマンドは同時ではなく順に実行される
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$OBS_DIR/normal.log"
```

括弧内は取得エラーで停止します。途中のエラーが出た場合は次の状態へ進まず、原因を確認して取り直してください。`tee -a`は同じ状態の再取得を追記し、以前のsnapshotを残します。各API取得は順次実行されるため、同一時刻の原子的な全体像ではありません。

続けて、**Aで**「転送状態の観測」の該当構成のコマンドを実行します。Calicoでは同じkindノード、Ciliumでは同じagentを正常・失敗・回復で比較します。Ciliumを使う場合の`CILIUM_POD`もAで設定します。

### A：対象だけをNotReadyにする

```bash
# ターミナルA
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").touch(); print(datetime.now(timezone.utc).isoformat(),"readiness-fail")' \
  2>&1 | tee -a "$OBS_DIR/readiness-fail.log"
k -n cni-lab wait --for=condition=Ready=false "pod/$TARGET_POD" --timeout=120s
```

Aの`wait`は対象PodのReady=Falseを待つだけで、全ノードの転送設定の反映完了までは保証しません。Bの既存TCPを止めず、Cの新規接続先と次の状態を確認します。

```bash
# ターミナルA
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /whoami --count 2
  k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /livez --count 1
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$OBS_DIR/not-ready.log"
```

もう一方のbackendがReadyのままか確認します。EndpointSliceでは対象のaddress・targetRefと`conditions.ready/serving/terminating`を対応付けます。IPそのものがSliceから消えるとは限りません。変更直後のSliceがまだ正常時の状態なら、Aで状態を取り直し、Cの接続先の変化と区別して記録します。

**回復させる前にAで**同じノードの規則/mapを再取得します。Cで対象Podが新規に選ばれなくなったこと、Bの既存接続が継続したか停止したかを記録します。NotReadyでもPod IPへの直接通信では`/whoami`と`/livez`が200を返す設計です。

probeログは`--prefix`でPod名・コンテナ名が付き、2 Podの行を区別できます。Pod間で時刻順に並ぶとは限らないため、付与されたUTC時刻を照合します。各snapshotで`START_UTC`以降を読み直すので、同じprobe行が再掲されることがあります。

### A：readinessを回復させる

```bash
# ターミナルA
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").unlink(missing_ok=True); print(datetime.now(timezone.utc).isoformat(),"readiness-recover")' \
  2>&1 | tee -a "$OBS_DIR/readiness-recover.log"
k -n cni-lab wait --for=condition=Ready "pod/$TARGET_POD" --timeout=120s
(
  set -e
  date -u +%FT%TZ
  k -n cni-lab get pods -l app=echo -o yaml
  k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
  k -n cni-lab logs -l app=echo -c echo --prefix --timestamps --tail=-1 --since-time="$START_UTC"
  k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
) 2>&1 | tee -a "$OBS_DIR/recovered.log"
```

**Aで**同じ規則/mapを再取得し、Cで両backendが再び選ばれるか確認します。Pod名・UID・IP・restartCountも正常時と照合します。Podが再作成されていた場合は、同じPodのreadinessだけを変えた比較として扱いません。

### 終了と読み取り方

記録後、**BとCでそれぞれCtrl+C**を押して通信観測を終了します。Aで両backendがReadyに戻っていることを確認してから、次のNetworkPolicy実験へ進みます。次節で使うPod IPもAで設定しておきます。

```bash
# ターミナルA
k -n cni-lab wait --for=condition=Ready pod -l app=echo --timeout=120s
k -n cni-lab get pods -l app=echo -o wide
POD_IP=$(k -n cni-lab get pod "$TARGET_POD" -o jsonpath='{.status.podIP}')
```

観測する関係は、probe応答 → Pod Ready → EndpointSliceのready → 転送規則/map → 新規接続先です。正常・失敗・回復で、利用可能なbackendが2→1→2へ変化する一方、通常ServiceのDNS回答はClusterIPのままと予想します。既存TCPが継続するか切断されるかは観測結果であり、必須成功条件ではありません。Ciliumのglobal backend mapにIPが残っていることだけで、新規選択対象だとは判定しません。

この基本手順のAPI状態は、Aが取得した時点のsnapshotです。probeの時刻、PodのlastTransitionTime、HTTPの時刻は残しますが、全段階の遷移時刻や正確な伝播遅延を測ったことにはしません。反映時間を調べる場合は、追加の観測ターミナルでPodとEndpointSliceを別々にwatchし、resourceVersion、受信UTC時刻、全Sliceの状態を保存します。watchの出力をA・B・Cへバックグラウンドで流し込まないでください。反映時間を製品の一般性能順位にしません。

参考：[readiness probe](https://v1-35.docs.kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/)、[EndpointSlice](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)、[ラベルとselector](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/)、[kubectl exec](https://v1-35.docs.kubernetes.io/docs/reference/kubectl/generated/kubectl_exec/)、[kubectl logs](https://v1-35.docs.kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/)

## 3：NetworkPolicyの両方向

Readyを回復させてから、Pod IP直接の新規接続で比較します。この段階ではDNSを使わず、接続失敗を名前解決失敗と混ぜません。

```bash
k apply -f experiments/cni-networking/policy-server-isolate.yaml
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k apply -f experiments/cni-networking/policy-server-allow.yaml
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k apply -f experiments/cni-networking/policy-client-isolate.yaml
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2
k apply -f experiments/cni-networking/policy-client-allow.yaml
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2
```

各操作の直後に観測し、次の予想と実測を照合します。失敗を期待するclient.pyの終了コード1はその段階の観測結果です。接続先の初期状態で両backendがReadyであることを確認します。

| 適用済みPolicy | allowed → Pod IP | denied → Pod IP | 確認する方向 |
| --- | --- | --- | --- |
| なし | 成功を予想 | 成功を予想 | 基準 |
| server-isolate | 失敗を予想 | 失敗を予想 | 宛先Ingress |
| 上記＋server-allow | 成功を予想 | 失敗を予想 | Ingressの許可の和集合 |
| 上記＋client-isolate | 失敗を予想 | 失敗を予想 | 送信元Egressも必要 |
| 上記＋client-allow | 成功を予想 | 失敗を予想 | 両方向の許可 |

`server-isolate`の空の許可は`server-allow`を打ち消しません。同じPod・同じ方向に適用される許可が和集合になります。Ingressの許可はEgressの隔離を解除しません。API作成と全nodeへの反映を同時とみなさず、新規接続と規則counter・dropイベントを照合します。

### Ingressの許可を追加して和集合を確認する

ここでは`server-isolate`と`server-allow`を残したまま、TCP 8080への全送信元許可を追加します。denied側のEgress隔離が結果を隠さないよう、先に`client-isolate`だけを外します。既存のTCPは使わず、新規接続で観測します。

```bash
k -n cni-lab delete networkpolicy client-isolate
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k apply -f experiments/cni-networking/policy-server-allow-all.yaml
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k -n cni-lab get networkpolicies
k -n cni-lab delete networkpolicy server-allow-all
k -n cni-lab exec client-denied -- python -B /app/client.py "$POD_IP" --count 2
k apply -f experiments/cni-networking/policy-client-isolate.yaml
k -n cni-lab exec client-allowed -- python -B /app/client.py "$POD_IP" --count 2
```

予想はdenied側が「追加前は失敗 → 全送信元許可の追加後は成功 → 削除後は失敗」です。最後にEgress隔離を戻した状態でも、allowed側は既存のclient-allowによって接続できると予想します。

### DNSだけを別に許可する

DNSのUDP/TCP 53は別の許可にします。kindのCoreDNS labelがサンプルと一致することを確認してから追加します。

```bash
k -n kube-system get pods -l k8s-app=kube-dns -o wide
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +time=1 +tries=1
k apply -f experiments/cni-networking/policy-client-dns.yaml
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +tcp +stats
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_FQDN" --count 2
```

`client-allow`と`client-dns`は同じallowed PodのEgressに適用されます。DNS許可を追加してもTCP 8080の許可は残ると予想します。既存TCPの挙動は新規許可判定と分けます。[NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)、[dig参照](https://bind9.readthedocs.io/en/stable/manpages.html#dig-dns-lookup-utility)

## 記録と片付け

公開する記録は、実験資材のcommit・imageID・設定、操作時刻、probe/Pod/Slice、規則/map、DNS、HTTPのPod名・UID・connection ID、確認できた範囲です。未採取・期待と異なる結果も残し、仕様、観測、解釈を分けます。pcapや未整理の全ログはGitへ入れず、必要な範囲を伏せて引用します。

[記録用テンプレート](record-template.md)へ予想・実測・解釈を分けて記入します。A/Bの記録は別々に作り、比較は共通条件が揃った部分に限ります。個人の作業状況や生ログは公開リポジトリへ書き込みません。

```bash
# 削除前に、作成したラボcontextとNamespaceのmanaged-by labelを確認する
k -n cni-lab get pods,service,networkpolicies
k get namespace cni-lab --show-labels
k delete namespace cni-lab
# 作成した方だけを削除する。別用途のkindクラスタを削除しない
kind delete cluster --name "$LAB_NAME"
```
