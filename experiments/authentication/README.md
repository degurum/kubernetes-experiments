# 認証経路と audit の境界: 最初の再現手順

[Issue #2](https://github.com/degurum/kubernetes-experiments/issues/2) の段階1〜3の核。以下は期待観測であり実機実証は未実施。[RBAC 手順](../api-server-rbac/) R1〜R11 と [共通 bootstrap](../shared/) を先に完了し、reader Binding を有効にしておく。新たな namespace や credential を永続作成しない。

## 条件とクライアント

例は repo root の Bash。`LAB_CONTEXT` は #1 と同じ明示 context。Python のコマンド名は既存環境に合わせる（例は `python`）。[auth_probe.py](../shared/auth_probe.py) は stdlib だけを使い、対象 ConfigMap API に一要求を送る。HTTPS/TLS 検証を維持する。proxy-url、tls-server-name、相対 CA パスを必要とする接続はこの最小 probe の対象外。環境側で証明書名と一致する直接 endpoint、embedded CA または絶対 CA パスを用意する。環境変数の HTTP proxy は使わない。

有効 token は `kubectl create token reader --duration=10m` を内部実行してメモリ内へ取得する。token を引数/環境変数/ファイル/出力へ書かず、管理者の client certificate/exec credential を HTTP request に載せない。TokenRequest は管理者の別 API 操作であり、その audit と SA 本人の対象要求を区別する。要求した有効期間と実際の発行期間は同じとは限らない。プロセス終了で token を手放すがメモリ消去の保証はない。端末録画・trace・debugger・core dump を使わない。

stdout は case、UTC 時刻、HTTP code、Audit-Id（あれば）、エラー型だけ。response body、Authorization や全 headers は出さない。期待が一致すれば exit 0、不一致や前提失敗なら非0。stdout も生の照合情報として repo 外に保持する。

## A1〜A4: HTTP に届く認証と認可

```bash
# A1: 有効 SA token、許可済み list -> 200
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case sa-list --expect 200
# A2: 同じ SA の有効 token、未許可 delete -> 403、sample は残る
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case sa-denied --expect 403
# A3: 合成 invalid bearer、client certificate はなし -> 401
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case invalid --expect 401
# A4: credential なし。anonymous 設定に応じた値を観測
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case anonymous
```

| Case | 期待応答 | audit で確認する点 |
| --- | --- | --- |
| A1 | 200 | `user.username=system:serviceaccount:api-audit-lab:reader`、通常の SA groups、list configmaps、decision=allow。`impersonatedUser` はない |
| A2 | 403 | 認証済み reader、delete configmaps/sample、decision=forbid。認証失敗ではない |
| A3 | 401 | user/groups・objectRef・認可 annotation が空/欠落でも固定補完しない。requestURI、userAgent、code、時刻、auditID と全 stage で探す |
| A4 | 403 または 401 | anonymous がこの path に許可され、RBAC grant がなければ system:anonymous/system:unauthenticated の認可拒否で403。anonymous 無効または path 限定で対象外なら401 |

A4 が200なら anonymous への予期しない権限等を調べ中止する。A1 が403なら認証が通ったか audit と照合し Binding/条件を確認する。TLS/接続失敗は401と分類しない。[公式 Authentication](https://kubernetes.io/docs/reference/access-authn-authz/authentication/) の無資格と invalid bearer の差を参照。

401 は `ResponseComplete` だけに絞らない。[v1.34.1 authn_audit.go](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/endpoints/filters/authn_audit.go) と [audit.go](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/endpoints/filters/audit.go) では認証失敗 handler の response writer が `ResponseStarted` を使う。利用版で再確認する実装例であり、一般の stage 説明だけから401の stage を決めない。

## 段階2: 補助ログで分かることを比較

1. A1/A3 の時刻、probe の Audit-Id と audit を確定する。
2. 同じ時間窓の apiserver 診断ログを環境側の interface で private に回収する。kind では apiserver Pod の標準出力、AWS kubeadm では全対象 CP の static Pod/runtime ログを扱う。採取コマンドは環境側で確定する。
3. 認証失敗エラーが存在するか、audit より具体的な原因があるかを比較する。通常の詳細度で原因が出なければ「未観測」。共通 ID がない場合、時刻だけで一対一対応を断定しない。
4. 詳細度引上げは後続の別試験として、対象/期間/rollback/容量/秘密露出の扱いを確定してから行う。この核は apiserver 設定を変更しない。

401 や失敗方式が得られても、token 検証の内部全履歴、実利用者、具体的な原因を必ず説明できるわけではない。RequestResponse は本文記録を増やす設定であり認証内部を自動トレースしない。TokenRequest/TokenReview 本文は秘密を含み得るため Metadata を保つ。CRD 追加でも認証内部/IdP/TLS は自動観測されない。

## A5/A6: API の HTTP 要求に届く前

有効な TLS 接続と audit の正の対照として直前/直後に A1 を行う。A5/A6 に token は作成/送信しない。

```bash
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case sa-list --expect 200
# A5: 信頼 CA をロードしない。TLS 検証を有効に保ったまま失敗
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case tls-untrusted
# A6: loopback の bind 済み・非 listen port。クラスタへの通信はない
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case connection-refused
python experiments/shared/auth_probe.py --context "$LAB_CONTEXT" --case sa-list --expect 200
```

| Case | クライアント期待 | audit/補助ログの境界 |
| --- | --- | --- |
| A5 | `SSLCertVerificationError`、HTTP code/Audit-Id は null | API endpoint に TLS 接続を試みるが HTTP request は送信完了しない。対応する HTTP audit は期待しない。サーバー側に TLS handshake 診断があり得る |
| A6 | `ConnectionRefusedError`、HTTP code/Audit-Id は null | ローカルの制御した接続拒否。apiserver を経由せず、対応する audit/サーバーログは期待しない。実ネットワーク障害の原因を再現したものではない |

timeout や別エラーなら期待一致ではなく前提違い。監査がないことだけを失敗箇所の証明にしない。正の対照、全 stage、rotation、全対象 CP、時間窓、audit backend のエラーを確認し、クライアントの具体的な失敗段階と合わせる。A5 はクライアントのサーバー証明書検証失敗であり、HTTP 到達後のクライアント証明書認証失敗とは別。

## 後続ケース（未実装・未実施）

同じ Issue 内で次を順に追加し、新規の重複テーマに分けない。

| 次のケース | 必要な条件と最小の比較 |
| --- | --- |
| SA audience 不一致 | api-audiences/issuer を確認し、API 用ではない合成 audience の短期 TokenRequest をメモリ内で作る。通常 audience 成功と401を比較。TokenReview 本文を保存しない |
| SA 期限切れ | 発行応答の expirationTimestamp を private に確認し、実際の期限後に同じ token で一回要求。要求 duration だけで期限を決めず、token を公開/永続化しない |
| exec plugin 失敗 | 一時 private kubeconfig と合成の非0終了 plugin。クライアント失敗と未送信を比較。既存 kubeconfig は上書きしない |
| client certificate 認証 | TLS handshake と HTTP 後の認証を分け、採用版 x509 条件を確認。秘密鍵は repo に入れない |
| OIDC/認証 webhook | 標準ケース後に方式を選ぶ。IdP ログイン/MFA/token 発行と API 要求を別採取し、共通 ID がない限界を記録 |
| kubelet 直接/API を通らない認証 | 必要性と endpoint/log 所在を調査する任意範囲。Pod アプリ独自ログインと API 認証を区別 |

最後に [RBAC の片付け](../api-server-rbac/#片付け) を行う。短期 token は保持しない。namespace/SA 削除とログ回収を確認し、クラスタの回収・破棄は環境側へ戻す。
