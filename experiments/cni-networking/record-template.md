# CNI実験の記録用テンプレート

このファイルは空の記録様式です。予想は[README](README.md)を参照し、実測欄は実行後に記入します。未採取の欄には未確認と記入し、結果を補って埋めません。生ログ・pcap・kubeconfig・秘密値はcommitせず、公開可能な抜粋だけを使います。

## 条件

| 項目 | 記録 |
| --- | --- |
| 構成 A / B、資材commit、実行日時UTC | |
| kind / Docker / kubectl / Helmの版 | |
| OS / kernel / architecture / node imageID | |
| Kubernetes / CNI / kube-proxyの版と設定 | |
| IPv4 / Pod・Service CIDR / MTU / tunnel | |
| backendとclientのnode / Pod名・UID・IP・restartCount | |
| CoreDNS image・replicas / Corefile / resolv.conf | |
| iptables legacy/nft / Cilium replacement・socket-LB | |

## 観測

| 段階 | 予想 | 実測と観測時刻UTC | 未確認の範囲 |
| --- | --- | --- | --- |
| Policyなし：Pod IP / ClusterIP / FQDNの新規接続 | | | |
| DNS：FQDNのUDP/TCP応答 / 短名search | | | |
| readiness正常：probe / Pod Ready / 全Slice / 規則・map | | | |
| readiness失敗：flag時刻 / probe / Ready / Slice / 新規backend | | | |
| readiness失敗：Pod IP直接 / 既存TCPのUID・connection ID | | | |
| readiness失敗中のFQDN回答 | | | |
| readiness回復：同じ観測点 / IP・UID・restartCount | | | |
| Ingress隔離 → allowed label許可 | | | |
| Egress隔離 → backend許可、Ingressとの両方向 | | | |
| Ingress全送信元許可の追加 → 削除、許可の和集合 | | | |
| DNSのEgress許可追加前後、TCP 8080許可の維持 | | | |

## 解釈と比較

| 項目 | 記録 |
| --- | --- |
| API状態と転送状態を対応づけられた範囲 | |
| ノードごとの反映差と観測方法の限界 | |
| 予想と異なった結果・再試行の条件 | |
| A/Bで揃った条件と揃わなかった条件 | |
| この観測から言えること / 言えないこと | |
| 後片付けの対象と確認結果 | |
