# Kubernetes Experiments

Kubernetesの挙動を段階的に検証し、再現できる手順・manifest・整理した結果・記事草稿を残す公開リポジトリです。API Server/RBAC と認証の最初の再現手順・最小 fixture を用意しています。クラスタでの実証は未実施です。

## 検証計画

| 計画 | 保存先 | 進め方 |
| --- | --- | --- |
| [API ServerとRBAC](https://github.com/degurum/kubernetes-experiments/issues/1) | [experiments/api-server-rbac](experiments/api-server-rbac/) | 正常なAPI操作と監査 → 許可・拒否 → 権限照会と実操作 → RBACの発展的な検証 |
| [認証経路と監査の限界](https://github.com/degurum/kubernetes-experiments/issues/2) | [experiments/authentication](experiments/authentication/) | 標準構成の認証 → 診断ログ → API到達前の失敗 → 必要時の外部認証・後続範囲 |

各段階で「仮説／条件／最小操作／応答とログ／結論・限界／後片付け」を記録します。最初の記事は各計画の1〜3を範囲とし、後続の検証は段階を分けます。PSAを含むAdmission制御は今回の対象外です。

## リポジトリの役割

実行は [共通前提・audit bootstrap](experiments/shared/) → [RBAC の R1〜R11](experiments/api-server-rbac/) → [認証の A1〜A6](experiments/authentication/) の順です。最初は使い捨て kind 1CP、後で同じ case を AWS kubeadm ラボへ移植します。環境構築や runtime/tool/provider の選定・インストールは別作業です。

状態・判断・実施結果の正本は common_private に置き、この公開 repo には再現手順・実装・期待観測・レビュー済みの無害化結果を置きます。

| リポジトリ | 管理するもの |
| --- | --- |
| kubernetes-experiments | 実験手順・manifest・整理した結果・記事草稿 |
| [automation_kubernetes](https://github.com/degurum/automation_kubernetes) | ラボの環境構築・回収 |

## ディレクトリ

- `experiments/`: 各検証の手順とmanifestを置く場所
- `results/`: 条件・観測事実・結論・限界を整理した結果
- `articles/`: 検証結果から作る記事草稿

## 公開する記録

補助スクリプトは `python -m unittest discover -s tests -v` でオフライン検証できます。テストは合成入力と mock だけを使い、クラスタの動作実証にはなりません。CI は既存の Trivy secret/misconfig check を行います。

秘密値、トークン、秘密鍵、Authorizationヘッダー、kubeconfig、生ログ、不要なAWS識別情報はcommitしません。ログ例は公開用に無害化した抜粋を使います。記事の公開は別途判断します。
