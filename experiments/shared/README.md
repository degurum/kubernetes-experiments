# 共通前提と bootstrap interface

この手順は使い捨ての kind 1 control-plane を最初の実行先とし、同じ case を後で AWS 上の kubeadm ラボへ移植する。クラスタ作成・AWS 操作・ツールのインストールは環境構築側で別途行う。本 repo の手順を用意したことは、実機検証の完了を意味しない。

## 環境構築側へ渡す条件

| Interface | 必要な条件 |
| --- | --- |
| 対象 | 他用途と共有しない使い捨てクラスタ。明示的な専用 context と、TLS 検証できる HTTPS endpoint/CA |
| 操作者 | fixture 作成/削除、reader の TokenRequest、ServiceAccount と必要 group の impersonation が可能なラボ管理者 |
| 認可 | 有効な認可方式/順序を記録。期待値は通常の Node,RBAC 構成で reader に追加の grant がないことが前提 |
| 認証 | SA token、管理者 credential、anonymous の有効範囲、issuer/api-audiences、認証設定ファイルの利用有無を記録。秘密値は記録しない |
| 監査 | [audit-policy.yaml](audit-policy.yaml) を起動前に配置。全要求を Metadata、全 stage を保持 |
| 回収 | audit JSONL と rotation ファイル、全対象 apiserver の診断ログを repo 外のアクセス制限された場所へ回収。UTC の操作開始/終了時刻も保持 |
| ツール | kubectl、認証 HTTP case 用 Python 3 標準ライブラリ。RBAC コマンド例は Bash。バージョンと入手手段は環境側で選択 |

`Policy` は Kubernetes API へ apply する CRD/リソースではない。kube-apiserver のファイル設定である。既存クラスタへ fixture を apply しても監査は有効にならない。[公式 Auditing](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/) の log backend と static Pod の mount 条件に従う。

必要な起動引数は `--audit-policy-file=/etc/kubernetes/experiment-audit-policy.yaml`、`--audit-log-path=/var/log/experiment-audit/audit.jsonl`、`--audit-log-mode=blocking`、`--audit-log-maxage=1`、`--audit-log-maxbackup=2`、`--audit-log-maxsize=10`。maxage は日、maxsize は MiB。短期ラボの目安であり、実際の使用量と回収間隔を確認する。policy を read-only、出力 directory を書込可能として apiserver に mount する。blocking でもディスク障害・欠落がない保証にはならない。

## kind の設定例（環境側で render、まだ実行しない）

[kind-audit.yaml.tmpl](kind-audit.yaml.tmpl) は 1CP 用の入力例。`__AUDIT_POLICY_ABSOLUTE_PATH__` を実在する policy ファイルの絶対パス、`__PRIVATE_AUDIT_DIRECTORY_ABSOLUTE_PATH__` を事前作成した repo 外の private directory に置換し、render 結果も repo 外に保存する。ホスト -> kind node の `extraMounts` と node -> apiserver static Pod の `extraVolumes` の両方が必要。[kind の Auditing](https://kind.sigs.k8s.io/docs/user/auditing/) を参照。

この例は [kubeadm v1beta4](https://kubernetes.io/docs/reference/config-api/kubeadm-config.v1beta4/) の `extraArgs`（name/value 配列）を使う。採用する kind/node image がこの API を受理するか、bootstrap 前に環境側で検証する。旧 v1beta3 の map 形式と混ぜない。kind、runtime、node image/digest はこの repo で固定せず、採用値を private 記録に残す。

環境側で作成を許可された時点の interface は `kind create cluster --name <lab-name> --image <selected-node-image> --config <rendered-private-config>`。今回は実行しない。ホスト出力 directory が空でない既存データなら別の directory を選ぶ。kind のノードログ一括回収だけに依存せず、mount した audit directory の現行/rotation ファイルも回収する。

AWS kubeadm 側は同じ policy/flags を kubeadm 設定と host mount に反映し、全 control-plane の監査・診断ログを収集する。kind 固有設定だけを置換し、namespace/SA/case/期待値は共通にする。IAM/IdP や実アカウント情報を fixture に加えない。

## 実行前の gate と照合

1. context/endpoint が専用ラボであることを確認。kubeconfig 全体や `--raw` の出力はターミナルへ表示・保存しない。
2. `kubectl --context "$LAB_CONTEXT" version -o json` で client/server 版を private 記録に残す。管理者は apiserver の実引数と mount を確認し、認証・認可・audit の条件を記録する。既存の設定/保護は変更しない。
3. [RBAC 手順](../api-server-rbac/) の R1 を一度実行し、対象 namespace/verb/resource、時刻、主体に合う audit を回収。正常応答と Metadata の照合ができるまで次へ進めない。
4. audit の全 stage を保持し、同じ `auditID` を一要求としてグループ化する。正常短期操作では通常 `RequestReceived` と `ResponseComplete`。401 は利用版により `ResponseStarted` も見る。401 に namespace/user/objectRef があるとは仮定せず、時刻・requestURI・userAgent・code でも探索する。

各操作は一回ずつ実行し、開始/終了時刻、exit code/HTTP code、取得した auditID を private 記録で結ぶ。kubectl の discovery や権限照会は別要求であり、一コマンド一イベントとは限らない。`--v=8/9`、shell trace、HTTP dump は credential を露出し得るので使わない。

JSONL の照合には stdlib の [audit_summary.py](audit_summary.py) を使える。以下は repo root の Bash で、`PRIVATE_AUDIT_DIR` は repo 外の回収先、`AUDIT_ID` は一要求の実測値。出力も private に保持する。条件は AND、空の抽出は exit 1。401 の探索には namespace 条件を付けない。

```bash
python experiments/shared/audit_summary.py "$PRIVATE_AUDIT_DIR"/audit*.jsonl --audit-id "$AUDIT_ID"
python experiments/shared/audit_summary.py "$PRIVATE_AUDIT_DIR"/audit*.jsonl \
  --code 401 --user-agent kubernetes-experiments/invalid
```

rotation ファイルの名前は環境側で確認し、必要なら明示 path を追加する。stage は絞らず全件返す。summary にも user/URI/reason 等が含まれ、匿名化済みの公開ログではない。

## 記録と公開

進捗・判断・実施結果の正本は common_private。ここには再現手順、fixture、期待観測と、レビュー済みの無害化結果だけを置く。Metadata にも user/groups、sourceIPs、URI、名前、userAgent、annotation が含まれる。抽出ツールは匿名化ツールではない。生ログ、HTTP headers、TokenRequest の応答、診断ログ、kubeconfig は commit しない。

[結果テンプレート](../../results/case-template.md) を使い、実施結果と予測を区別する。実ログを公開する場合は抜粋の field を個別審査し、値を合成値に置換する。記事は [構成案](../../articles/api-audit-outline.md) に沿って、確認できた case のみを事実として執筆する。

## 公式資料の確認範囲

仕様の確認日: 2026-10-08。オンライン docs は更新されるため、実行時の server 版に対応する docs/source でも再確認する。

- [認証](https://kubernetes.io/docs/reference/access-authn-authz/authentication/): 有効な token、無資格、invalid bearer の違い。
- [認可](https://kubernetes.io/docs/reference/access-authn-authz/authorization/)・[RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/): 認証後の認可、additive な grant、namespace/subresource と権限照会。
- [v1.34.1 authn_audit.go](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/endpoints/filters/authn_audit.go)・[audit.go](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/endpoints/filters/audit.go): 認証失敗 handler と response writer の stage。v1.34.1 の実装例であり、採用版全般の固定期待値ではない。
