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

進む順序は「Aの準備 → サンプル → DNS/転送 → readiness → Policy → 記録/片付け → Bで同じ手順」です。停止・watch・新規接続loop用に複数のターミナルを使います。各ターミナルでリポジトリのルートへ移動し、次の設定を入れてください。Bのときだけ`LAB_NAME=cni-cilium`へ替えます。k関数は毎回contextを指定するため、現在のkubectl contextを変更しません。

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

別ターミナルで、Serviceへ一つのHTTP/1.1接続を維持します。最初のJSONの `backend.pod` を切替対象にします。client.pyは同じPod UID・connection IDを確認し、切断時は自動再接続せず停止します。

```bash
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --mode keepalive --count 180
```

操作側でも同じLAB_CONTEXTとk関数を設定します。対象名は上の出力から選び、勝手に別Podを選びません。

```bash
read -r -p '既存接続の初回JSONに出たbackend Pod名: ' TARGET_POD
k -n cni-lab get pod "$TARGET_POD" -o yaml
TARGET_IP=$(k -n cni-lab get pod "$TARGET_POD" -o jsonpath='{.status.podIP}')
```

logs用ターミナルにも同じTARGET_PODを設定してから、probe応答を読みます。

```bash
k -n cni-lab logs -f "$TARGET_POD" --timestamps
```

さらに二つのターミナルを使い、一方では同じTARGET_PODを設定してPod Readyをwatchし、もう一方ではService labelで取得した全EndpointSliceをwatchします。resourceVersion、ReadyのlastTransitionTime、Sliceのaddress・conditions.ready/serving/terminatingを保存し、観測側のUTC時刻も付けます。

```bash
k -n cni-lab get pod "$TARGET_POD" --watch --output-watch-events -o json
```

```bash
k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo --watch --output-watch-events -o json
```

さらに、新規接続loopを別に動かします。このターミナルではService IPを再取得します。watchとlogsは別々のターミナルで動かし、Ctrl+Cで止めます。

```bash
SERVICE_IP=$(k -n cni-lab get service echo -o jsonpath='{.spec.clusterIP}')
k -n cni-lab exec client-allowed -- python -B /app/client.py "$SERVICE_IP" --mode new --count 120
```

操作ターミナルでは、logsやwatchを走らせず、先にTARGET_PODとTARGET_IPを設定します。flag変更と、その後の状態を順に記録します。失敗中にも対象Pod IPへ直接アクセスし、データ応答を確認します。

```bash
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").touch(); print(datetime.now(timezone.utc).isoformat(),"readiness-fail")'
k -n cni-lab wait --for=condition=Ready=false "pod/$TARGET_POD" --timeout=120s
k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /whoami --count 2
k -n cni-lab exec client-allowed -- python -B /app/client.py "$TARGET_IP" --path /livez --count 1
date -u +%FT%TZ
k -n cni-lab get pod "$TARGET_POD" -o yaml
k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
```

回復させる前に、上の「転送状態の観測」と同じノードの規則/mapを再取得し、新規接続loopのbackendと照合します。DNS用のターミナルでFQDNも再問い合わせます。観測loopが終了していた場合は新しい観測として開始し直し、既存TCPが切断された観測とは区別します。記録後、操作ターミナルで回復させます。

```bash
k -n cni-lab exec "$TARGET_POD" -- python -c 'from pathlib import Path; from datetime import datetime,timezone; Path("/state/not-ready").unlink(missing_ok=True); print(datetime.now(timezone.utc).isoformat(),"readiness-recover")'
k -n cni-lab wait --for=condition=Ready "pod/$TARGET_POD" --timeout=120s
date -u +%FT%TZ
k -n cni-lab get pod "$TARGET_POD" -o yaml
k -n cni-lab get endpointslices -l kubernetes.io/service-name=echo -o yaml
```

回復後も同じ規則/map、FQDN、新規接続を再取得します。

観測する連鎖は、kubeletのprobe応答→Pod Ready→EndpointSlice ready→転送規則/map→新規接続先です。API内のIPが必ず消えるとは扱わず、Ciliumのglobal backend mapにIPが残るだけでも新規選択対象とは判定しません。既存接続が継続するか切断されるかは結果であり、必須成功条件にはしません。

正常・失敗・回復の各状態で、以下を実行してFQDNを再問い合わせます。

```bash
k -n cni-lab exec dns-probe -- dig "$SERVICE_FQDN" A +stats
```

予想は、通常Serviceの回答がClusterIPのままで、新規接続の対象がReadyなbackendへ変わることです。今回は別backendを常にReadyに保つので、2→1→2の変化と通常Serviceの回答ClusterIPが変わらないことを分けて確認します。反映時間を製品の一般性能順位にしません。[probe](https://kubernetes.io/docs/concepts/workloads/pods/probes/)、[EndpointSlice](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)

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
