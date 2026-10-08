# API 応答と audit を照合する記事の構成案

**構成案。実験・記事公開は未実施。** 実証後の [結果](../results/) を根拠とし、予測を観測事実へ置換する。進捗の正本は common_private。

## 最初の記事: API Server と RBAC（#1 段階1〜3）

1. 認証済み要求 -> 認可 -> 操作応答の流れ、Metadata に残る field と残らない本文。
2. R1 の list 一回を、時刻・auditID・stage・主体・resource・HTTP code で対応づける。
3. R2/R3/R8 の Binding なし -> あり -> なしを並べる。管理者の変更と impersonated reader の要求を別に扱う。
4. R4〜R7 の verb/namespace/resource/subresource の違い。forbid と RBAC additive、reason の限界。
5. R9/R10 の can-i/SSAR と実操作。R11 の allow + 404 で認可と操作成功を分ける。
6. 再現条件、期待不一致の停止条件、片付け、未実施の発展範囲。

## 続編: 認証経路と audit の限界（#2 段階1〜3の核）

1. A1/A2 と impersonation の差。実 SA token は一時生成し credential を公開しない。
2. A3/A4 の401/403と anonymous 条件。401 の stage/主体欠落を採用版 source と照合する。
3. 通常診断ログを補助とし、失敗方式・原因・利用者のうち確認できるものを分ける。
4. A5/A6 と正常要求の正の対照。HTTP 前の TLS/接続失敗と audit がないことの解釈。
5. expires/audience/exec/外部認証の次段階、CRD・RequestResponse で自動観測されない範囲。

各節を「仮説 / 条件 / 最小操作 / 応答と無害化ログ / 結論・限界」に揃える。公開前に全抜粋を審査し、公開の判断は別途行う。
