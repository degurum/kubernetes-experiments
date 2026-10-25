# CNI・Service・NetworkPolicyの実測（2026-10-25）

[PR #7](https://github.com/degurum/kubernetes-experiments/pull/7)の[実験手順](../experiments/cni-networking/README.md)に対応する記録です。予想だけでなく、CalicoとCiliumの両方を実際に操作して確認しました。生ログ、kubeconfig、個人の端末名やパスは掲載していません。

## 条件と検証範囲

初回実測の資材はcommit `4f8ae0c4a6faad6d978bc4f4b83166b1ab416365`。Calicoは既存の3ノードとサンプルを再利用し、Ciliumは別名の3ノードを新規作成しました。実測前にConfigMap内のPythonがcheckoutと一致すること、PolicyとPVCがないことを確認しました。Calicoの新規構築を今回やり直したわけではありません。

| 条件 | 実値 |
| --- | --- |
| OS / architecture | Ubuntu 24.04 WSL2 / amd64 |
| kernel | `6.18.33.2-microsoft-standard-WSL2` |
| kind / Docker Engine / kubectl / Helm | v0.31.0 / 29.9.1 / v1.35.9 / v3.22.0 |
| Kubernetes / node | v1.35.0、1 control-plane＋2 worker、Debian 12 kind image |
| Calico | v3.33.0、Iptables dataplane、VXLAN、BGP無効 |
| CalicoのService転送 | kube-proxy iptables、ノードのiptablesはv1.8.9（nf_tables） |
| Cilium | v1.20.2、VXLAN、kube-proxy replacement有効、kube-proxy DaemonSetなし |
| Ciliumのsocket-LB | statusでEnabled、Coverage Fullを確認 |
| Pod / Service CIDR | IPv4 `10.244.0.0/16` / `10.96.0.0/12` |
| Pod eth0 MTU | 両構成で1400 |
| CoreDNS / replicas / cluster domain | v1.13.1 / 2 / cluster.local |
| サンプル | backend 2 Podを別workerへ配置、両clientとdns-probeはworker one |
| Service | ClusterIP、TCP 8080、sessionAffinity None、internalTrafficPolicy Cluster |

Kubernetes node、Python、DNSプローブのimage digestは[各manifest](../experiments/cni-networking/)で固定しています。初回のCalico測定は10:45:05–10:46:42 UTC、Ciliumは10:55:52–10:57:28 UTCです。各構成41個の判定が通過しました。ここでいう判定には、想定した拒否を実際に確認したものも含みます。

## 観測結果

| 操作・確認 | Calico | Cilium |
| --- | --- | --- |
| 3ノード、CNI、CoreDNS、サンプルのReady | 確認 | 確認 |
| Policyなしでallowed / deniedから両backendのPod IPへ接続 | 同一・別workerともHTTP 200 | 同一・別workerともHTTP 200 |
| ClusterIPへの新規TCP 20回 | HTTP 200、両backendを観測 | HTTP 200、両backendを観測 |
| FQDNのDNS回答、UDP / TCP | ClusterIPを回答 | ClusterIPを回答 |
| 短名のsearchと短名 / FQDNのHTTP | 成功 | 成功 |
| 対象1 Podのreadinessを失敗・回復 | Readyなbackendが2→1→2 | Readyなbackendが2→1→2 |
| NotReady時の新規TCP 20回 | 対象への選択0回、他方が200 | 対象への選択0回、他方が200 |
| 回復後の新規TCP 30回 | 両backendを再び観測 | 両backendを再び観測 |
| NotReadyのPod IPへ直接 `/whoami` / `/livez` | ともに200 | ともに200 |
| 正常・NotReady・回復のDNS回答 | 同じClusterIP | 同じClusterIP |
| 既存TCP（0.2秒間隔） | 73応答を観測、同じPod・UID・connection ID、エラー0 | 80応答を観測、同じPod・UID・connection ID、エラー0 |
| 比較中のbackend名・UID・IP・restartCount | 変化なし | 変化なし |

NotReady時もEndpointSlice内に対象のaddressとtargetRefは残り、対象は`ready=false / serving=false / terminating=false`になりました。他方はReadyのままです。回復後は両方がreadyになりました。API取得と転送状態の採取は順次で、同一時刻の原子的なsnapshotではありません。

Calicoではclient側workerのClusterIPに対応する`KUBE-SVC`から`KUBE-SEP`、DNAT先をたどり、選択可能なbackendが2→1→2になることを確認しました。Ciliumでは両workerの同じagentで`service list`と`bpf lb list`を採取しました。対象はService一覧に`maintenance`として残る一方、該当ServiceのBPF選択slotから外れ、回復後に戻りました。IPが一覧に残るだけで新規選択対象とは判定できません。

## NetworkPolicy

基本4段階は、両clientから同一worker・別workerの両backendへ新規TCPを作って確認しました。許可はHTTP 200・JSON errorなし・終了コード0、拒否は実際の接続エラーJSONと終了コード1です。`kubectl exec`自体の失敗を拒否として数えていません。

| 累積する操作 | allowed | denied | 両構成の結果 |
| --- | --- | --- | --- |
| `server-isolate` | 拒否 | 拒否 | 期待どおり |
| `server-allow`追加 | 200 | 拒否 | 期待どおり |
| `client-isolate`追加 | 拒否 | 拒否 | 期待どおり |
| `client-allow`追加 | 200 | 拒否 | 期待どおり |
| client-isolateを外し、server-allow-allを追加→削除 | — | 拒否→200→拒否 | 許可の和集合を確認 |
| client-isolateを戻す | 200 | — | client-allowの許可は残る |
| client-dns追加前→追加後 | DNS timeout→UDP/TCP回答、FQDN HTTP 200 | — | DNSとbackendのEgress許可を別々に確認 |

初回測定は各通信1～2回、Policy適用後に1秒置いて実行しました。一般にその時間で反映が完了するという保証ではありません。手順では取得時刻と結果を残し、非同期反映と観測失敗を区別するようにしています。

## 実測で修正した点

6個のkindノードを同時起動しようとした際、Cilium導入前のノード内systemdがinotify資源不足で終了しました。kindの後続エラーは起動完了ログを見つけられないという内容でした。当時の`fs.inotify.max_user_instances`は128です。失敗ログを保存し、今回作成に失敗したノードだけを片付け、Calicoの3ノードをデータを保持して停止したところ、Ciliumの構築が成功しました。ホストの上限、認証、セキュリティ設定は変更していません。[kindの既知の問題](https://kind.sigs.k8s.io/docs/user/known-issues/#pod-errors-due-to-too-many-open-files)とも整合します。

また、TTYなしの`kubectl exec`をローカルで中断すると、Pod内のkeepaliveプロセスが残ることを確認しました。接続端末は`kubectl exec -it`へ修正し、client.pyはCtrl+Cを`event: interrupted`と終了コード130で記録します。通常の通信エラー、有限回数による完了、手動終了を区別するための変更です。

## 限界

- 両構成の実行条件を記録した機能比較です。性能・負荷耐性・均等分散の優劣は測っていません。
- Ciliumのagent statusには、kernelがServiceのsocket terminationをサポートしない旨のDEGRADEDがありました。DNS・Service・Policyの上記操作は通過しましたが、既存TCPの結果を別kernelや全Cilium構成へ一般化しません。
- 全ノードの反映完了時刻、厳密な伝播遅延、conntrack全経路、pcap、DNS metricsは未測定です。全Sliceと転送状態を順に読み取ったsnapshotです。
- 初回の自動操作では手打ちより短い回数・間隔を使いました。人間による30分の連続観測や全ブロックを無条件に一括貼り付けする使い方は検証対象外です。


## 改稿後のコマンドと終了状態

改稿後はreadiness節のBashブロックを2つの独立したシェルで再実行し、接続端末に実際のTTYを割り当ててCtrl+Cを入力しました。正常・NotReady・回復の各12回の新規TCPは両構成で2→1→2 backendを観測し、すべてHTTP 200。NotReadyの直接 `/readyz` は503、比較中のPod名・UID・IP・restartCountは不変でした。

| 最終確認 | Calico | Cilium |
| --- | --- | --- |
| UTC | 11:16:29–11:17:00 | 11:12:06–11:12:35 |
| 同じ既存TCPで確認した応答数 | 29 | 28 |
| Ctrl+C | interruptedイベント、通信プロセスの終了コード130 | 同左 |
| 終了後に残ったclient.pyプロセス | 0 | 0 |
| 主端末と接続端末の保存先 | 異なるディレクトリ | 異なるディレクトリ |

最終的に、Calicoは同じ3個のノードコンテナーを稼働状態へ戻しました。3ノード、Calico、CoreDNS 2 Pod、サンプル5 PodがReady、API `/readyz` はok、NetworkPolicyは0です。Ciliumはreadiness回復・Policy片付け後に3ノードを停止し、コンテナーとデータを保持しました。停止後のCilium再起動は今回の検証範囲外です。元からあるリポジトリの作業と操作ログは保持しています。

再開はCalicoで[手順の0節](../experiments/cni-networking/README.md#0-主端末を準備する)の変数設定から始め、1節で既存クラスタとサンプルを確認します。create/installを繰り返さず、資材のdiffがなければ2節のDNS/Serviceへ進めます。

静的検証はREADMEのBash 33ブロック、Python 2ファイルの構文、相対ファイル参照、git diff --checkを確認しました。CIと同じTrivy 0.75.0で秘密検出なし、実験manifestのHIGH/CRITICAL設定問題なしです。独立レビューで見つかった両workerの観測漏れ、denied側の確認漏れ、終了コード保存、既存サンプルの一致確認は修正しています。
