# CNI実験の記録用テンプレート

構成ごとに1部作成し、実行後に実測を記入します。予想と実測を混ぜず、採取しなかった項目は「未確認」と書きます。整理した結果だけを `results/` に置き、生ログ、pcap、kubeconfig、秘密値、個人の絶対パスや不要な識別子は公開しません。

## 条件

| 項目 | 記録 |
| --- | --- |
| 構成：Calico / Cilium、実験資材commit、実行日時UTC | |
| 新規作成 / 既存クラスタを確認して再利用 | |
| kind / Docker / kubectl / Helmの版 | |
| OS / kernel / architecture / node imageID | |
| Kubernetes / CNI / kube-proxyの版と設定 | |
| IPv4 / Pod・Service CIDR / MTU / tunnel | |
| backendとclientのnode、Pod名・UID・IP・restartCount | |
| CoreDNS image・replicas / Corefile / resolv.conf | |
| Calico iptables legacy/nft / Cilium replacement・socket-LB | |
| セッションID、readiness観測ID、主端末・接続端末の対応 | |
| API状態の取得方法：基本snapshot / 追加watch | |

保存先の実際の絶対パスは手元の記録にだけ残し、公開欄へ転記しません。再実行したら新しい観測IDを付け、旧観測を消さずに区別します。

## 観測

| 段階 | 予想 | 実測・観測時刻UTC | 未確認の範囲 |
| --- | --- | --- | --- |
| 開始条件：3 node・CNI・CoreDNS、backend 2 Podとclient 2 Pod・dns-probe 1 Pod、Policyなし | | | |
| Policyなし：同一worker・別workerのPod IP / ClusterIP / FQDNの新規接続、JSON status/error/終了コード | | | |
| DNS：FQDNのUDP/TCP回答、短名search、Service ClusterIP | | | |
| readiness正常：probe / Pod Ready / 全EndpointSlice / 有限回の新規TCP | | | |
| 既存TCP開始：初回backend Pod・UID・connection ID、接続端末の開始時刻 | | | |
| readiness失敗：flag・probe / Ready / Slice / 有限回の新規TCP | | | |
| readiness失敗：Pod IP直接の `/whoami` と `/readyz`、HTTP status | | | |
| readiness失敗：既存TCPの継続または終了、UID・connection ID・終了理由 | | | |
| readiness失敗中のFQDN回答 | | | |
| readiness回復：同じ観測点 / IP・UID・restartCount / 有限回の新規TCP | | | |
| Ingress隔離：allowed・deniedの拒否 | | | |
| Ingressのallowed許可：allowedは200、deniedは拒否 | | | |
| Egress隔離：allowed・deniedの拒否 | | | |
| Egressのbackend許可：allowedは200、deniedは拒否 | | | |
| Ingress全送信元許可の追加・削除：deniedの拒否→200→拒否 | | | |
| DNS Egress許可の追加前後、TCP 8080許可の維持 | | | |
| 補助観測：Calico規則・conntrack / Cilium map / watch（実施時だけ） | | | |

通信の「成功」は `client.py` の終了コード0だけでなく、各JSONの `status: 200` と `error` がないことを確認します。拒否を期待した段階ではJSON `error` と終了コード1を記録し、`kubectl exec` の失敗やJSON出力なしと区別します。`/readyz` の503はアプリが意図して返すHTTP応答で、Pythonが終了コード0になる場合があります。

基本手順のAPI状態は順番に採取したsnapshotです。probe行を再取得しても新しいprobeとは数えません。全遷移の厳密な時刻や伝播遅延は、追加watch等を採取していなければ未確認とします。

## 解釈と比較

| 項目 | 記録 |
| --- | --- |
| API状態と転送状態を対応づけられた範囲 | |
| ノードごとの反映差と観測方法の限界 | |
| 予想と異なった結果・再試行の条件 | |
| Calico/Ciliumで揃った条件と揃わなかった条件 | |
| この観測から言えること / 言えないこと | |
| 終了時のPod Ready、残ったPolicy、片付けの対象と確認結果 | |

接続端末では `kubectl exec -it` を使い、Ctrl+C後の `event: interrupted` と終了コード130を手動終了として記録します。TTYではremote stdout/stderrが合流するため、通信応答のJSONと終了イベントを区別します。各観測ディレクトリの `exit-codes.log` も照合してください。
