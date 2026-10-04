# Kubernetes Experiments

Kubernetesの挙動を段階的に検証し、再現できる手順・manifest・整理した結果・記事草稿を残す公開リポジトリです。現在は検証計画と記録用の最小構成を用意した段階です。

## 検証計画

| 計画 | 保存先 | 進め方 |
| --- | --- | --- |
| [API ServerとRBAC](https://github.com/degurum/kubernetes-experiments/issues/1) | [experiments/api-server-rbac](experiments/api-server-rbac/) | 正常なAPI操作と監査 → 許可・拒否 → 権限照会と実操作 → RBACの発展的な検証 |
| [認証経路と監査の限界](https://github.com/degurum/kubernetes-experiments/issues/2) | [experiments/authentication](experiments/authentication/) | 標準構成の認証 → 診断ログ → API到達前の失敗 → 必要時の外部認証・後続範囲 |

各段階で「仮説／条件／最小操作／応答とログ／結論・限界／後片付け」を記録します。最初の記事は各計画の1〜3を範囲とし、後続の検証は段階を分けます。PSAを含むAdmission制御は今回の対象外です。

## リポジトリの役割

| リポジトリ | 管理するもの |
| --- | --- |
| kubernetes-experiments | 実験手順・manifest・整理した結果・記事草稿 |
| [automation_kubernetes](https://github.com/degurum/automation_kubernetes) | ラボの環境構築・回収 |

ラボの前提は [automation_kubernetes PR #4](https://github.com/degurum/automation_kubernetes/pull/4) を参照してください。実機検証の実施・完了は各Issueで記録します。

## ディレクトリ

- `experiments/`: 各検証の手順とmanifestを置く場所
- `results/`: 条件・観測事実・結論・限界を整理した結果
- `articles/`: 検証結果から作る記事草稿

## 公開する記録

秘密値、トークン、秘密鍵、Authorizationヘッダー、kubeconfig、生ログ、不要なAWS識別情報はcommitしません。ログ例は公開用に無害化した抜粋を使います。記事の公開は別途判断します。
