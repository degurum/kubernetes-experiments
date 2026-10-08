# Case 結果テンプレート

**テンプレート・実施結果ではない。** 状態・判断の正本は common_private。生ログは repo 外に保持し、以下は検証後に無害化して公開可能な情報だけを記入する。

| 項目 | 記入内容 |
| --- | --- |
| Issue / case | #1 R1〜R11 または #2 A1〜A6 の一つ |
| 実施状態・日時 | 未実施 / 実施済み、UTC 日時（公開時は必要な粒度へ） |
| 条件 | server/client 版、kind/node image または kubeadm 版、認証/認可方式と順序、anonymous 条件、Metadata policy と全 stage、fixture commit |
| 仮説 | 期待 HTTP/exit code、audit の対象 field |
| 最小操作 | 再現コマンドへの参照。credential は含めない |
| API 応答 | 実測 HTTP/exit code。SSAR は照会 HTTP と allowed を分ける |
| 対応 audit | 照合方法、stage、verb/resource/subresource/namespace、user と impersonatedUser の違い、code/decision。auditID 等は合成値に置換 |
| 補助ログ | 得られた原因情報と照合根拠、共通 ID がない限界 |
| 観測事実 | 実際に確認したもののみ |
| 結論・限界 | 仮説一致/不一致、分からないこと、収集欠落の可能性 |
| 片付け | namespace/SA 削除、NotFound 確認、回収・環境側破棄の完了/未完了 |

公開前に user/groups、sourceIPs、URI/query、userAgent、annotations、名前、時刻、ID を個別審査する。token、秘密鍵、Authorization、kubeconfig、TokenRequest/TokenReview 本文、実 AWS 情報を含めない。期待出力を実測ログとして掲載しない。
