# API ServerとRBACの検証

計画・チェックリスト: [Issue #1](https://github.com/degurum/kubernetes-experiments/issues/1)

このフォルダに、段階ごとの最小操作、試験用manifest、後片付け手順を追加します。

1. 正常なAPI操作とMetadata監査を対応づける
2. RBACの許可・拒否とRoleBinding変更を比較する
3. 権限照会と実際のAPI操作を比較する
4. 隔離した試験範囲でRBACの安全性とCRDを確認する

認証経路は [別計画](../authentication/) で扱います。PSAを含むAdmission制御は今回の対象外です。実機検証は未実施で、manifest・結果は今後の検証に合わせて追加します。
