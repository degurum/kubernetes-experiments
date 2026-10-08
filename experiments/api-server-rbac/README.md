# API Server と RBAC: 最初の再現手順

[Issue #1](https://github.com/degurum/kubernetes-experiments/issues/1) の段階1〜3。まず正常応答と Metadata audit を一つ対応づけ、その後に許可/拒否、Binding 変更、権限照会を比較する。以下は期待値であり、クラスタでの実証結果ではない。認証の比較は [Issue #2 の手順](../authentication/) に進む。

## 条件・最小 fixture

[共通前提](../shared/) の bootstrap とログ回収 gate を完了した専用ラボだけで実行する。例は repo root の Bash。`LAB_CONTEXT` は環境側から受け取る明示 context（kind なら通常 `kind-<lab-name>`）。現在の context を変更せず、各操作に指定する。

```bash
export LAB_CONTEXT='kind-api-audit-lab'  # 実際の専用ラボ名に置換
kubectl --context "$LAB_CONTEXT" version -o json
kubectl --context "$LAB_CONTEXT" get namespace api-audit-lab api-audit-other
```

二つの namespace が両方 NotFound であることを確認する。既存なら共有データへ apply/delete せず中止し、所有者と再実行条件を確認する。想定外の接続/認証エラーも NotFound と取り違えない。

```bash
kubectl --context "$LAB_CONTEXT" apply -f experiments/api-server-rbac/fixtures/base.yaml
```

`base.yaml` は専用 namespace 二つ、reader SA、合成 ConfigMap 二つ、ConfigMap の get/list のみの Role を作る。Binding は別ファイル。Pod、Secret、ClusterRoleBinding、wildcard は不要。`automountServiceAccountToken: false` は Pod 自動 mount を避ける設定であり、TokenRequest 自体は禁止しない。

初期 RBAC 検証は管理者 credential で認証し、SA を impersonate する。SA token の認証試験ではない。group も実 SA の通常の group に合わせる。操作者自身に impersonation 権限が必要だが、新たな広域 grant は作らない。

```bash
AS_READER=(--as=system:serviceaccount:api-audit-lab:reader \
  --as-group=system:serviceaccounts \
  --as-group=system:serviceaccounts:api-audit-lab \
  --as-group=system:authenticated)
```

## R1: 正常 list 一回と audit の対応

仮説: 管理者による namespaced list は HTTP 200 となり Metadata に要求情報が残る。

```bash
date -u +%FT%TZ
kubectl --context "$LAB_CONTEXT" get --raw=/api/v1/namespaces/api-audit-lab/configmaps
date -u +%FT%TZ
```

管理者の `user`、`verb=list`、`objectRef.resource=configmaps`、`namespace=api-audit-lab`、`responseStatus.code=200`、`auditID`、認可 annotation を照合する。`requestObject` / `responseObject` は Metadata では記録しない。管理者名は公開用の固定値ではない。回収できない/条件が違う場合は bootstrap を確認し R2 へ進まない。

## R2〜R8: 許可・拒否・Binding 変更

各コマンドの exit code と操作時刻を記録し、対応する audit を照合してから次へ進む。拒否の exit code は非0（通常1）。`--raw` は discovery を避けて対象 API を明示する。初期 deny が allow なら、既存 ClusterRoleBinding 等の追加 grant があるため中止する。

```bash
# R2: Role はあるが Binding はない -> Forbidden / 403
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/configmaps

# R3: 管理者が Binding を追加 -> reader list は 200
kubectl --context "$LAB_CONTEXT" apply -f experiments/api-server-rbac/fixtures/binding.yaml
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/configmaps

# R4: 同 resource、未付与の verb delete -> 403、sample は残る
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" -n api-audit-lab delete configmap sample

# R5: 同 verb/resource、別 namespace -> 403
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-other/configmaps

# R6: 同 namespace/list、別 resource pods -> 403
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/pods

# R7: 未付与の subresource pods/log -> 403（Pod を作らなくてよい）
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/pods/not-created/log

# R8: 管理者が Binding を削除 -> reader list は再び 403
kubectl --context "$LAB_CONTEXT" -n api-audit-lab delete rolebinding reader-binding
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/configmaps
```

| Case | 期待される主な audit |
| --- | --- |
| R2/R8 | list configmaps / api-audit-lab、403、decision=forbid |
| R3 | 管理者の RoleBinding 作成（初回は create/201）と、reader の list/200 を別要求として確認 |
| R4 | delete configmaps/sample、403 |
| R5/R6/R7 | それぞれ namespace/resource/subresource の差、403 |
| Binding 削除 | 管理者の delete rolebindings/reader-binding、成功 code を確認 |

impersonation では audit の `user` は元の操作者、`impersonatedUser` が reader。reader としての認可を見たことと、reader credential を認証したことを分ける。403 の `authorization.k8s.io/decision=forbid` は最終判断であり、RBAC の明示 Deny ルールではない。RBAC は grant の加算である。`reason` の値は利用版・authorizer に依存し、全ルールの評価履歴にはならない。[公式 RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/) と [認可](https://kubernetes.io/docs/reference/access-authn-authz/authorization/) を参照。

変更直後に期待と違う場合は informer の反映時間も考慮し、時刻を残して少数回だけ再試行する。変化しなければ追加 grant、誤 context、subject/roleRef、authorizer 順序を調べ、中止する。失敗を無条件に成功へ読み替えない。

## R9〜R11: can-i / SSAR と実操作

Binding を戻す。`can-i` は内部で SelfSubjectAccessReview を作る。権限照会要求自体の成功と `.status.allowed` は別の値であり、Metadata だけでは allowed 本文を読めない。[公式 can-i](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_auth/kubectl_auth_can-i/) を参照。

```bash
kubectl --context "$LAB_CONTEXT" apply -f experiments/api-server-rbac/fixtures/binding.yaml

# R9: yes / exit 0 と、実 list/200
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" auth can-i list configmaps -n api-audit-lab
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  create -f experiments/api-server-rbac/fixtures/ssar-list.yaml -o json
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/configmaps

# R10: no / 非0 と、実 delete/403
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" auth can-i delete configmaps/sample -n api-audit-lab
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" -n api-audit-lab delete configmap sample

# R11: 認可 yes でも存在しない名前の get は 404
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" auth can-i get configmaps/not-created -n api-audit-lab
kubectl --context "$LAB_CONTEXT" "${AS_READER[@]}" \
  get --raw=/api/v1/namespaces/api-audit-lab/configmaps/not-created
```

R9 の SSAR は `create selfsubjectaccessreviews`、通常 HTTP 201（照会成功）、応答本文の `allowed=true`。R10 の can-i も照会要求は成功し得るが、判定は false。SSAR は保存されない review API なので削除不要。R11 は decision=allow、responseStatus.code=404。認可 allow は存在・妥当性・Admission 等を含む操作全体の成功保証ではない。Admission の試験は今回広げない。

## 段階4の後続範囲（未実装・未実施）

bind/escalate/impersonate と wildcard/CRD は同じ Issue の発展編。今回の `--as` は観測の手段であり、impersonation 権限そのものの安全性試験を完了したものではない。

次回は namespace を別に分け、比較する権限を一つずつ追加して、Role 作成と escalate、RoleBinding 作成と bind、User/Group/SA の impersonate をそれぞれ API 応答と audit で照合する。必要な特権と rollback を事前確定し、cluster-admin や wildcard を既存 namespace に付与しない。CRD は合成カスタムリソースへの get/list と audit を比較する題材にする。CRD 自体は認証内部・IdP・TLS を自動観測しない。

## 片付け

#2 を続けるなら Binding を有効にしたまま fixture を保持する。全 case 完了後、管理者による sample の存在確認、ログ回収を行う。R4/R10 が想定外に成功していれば条件違反として記録する。

```bash
kubectl --context "$LAB_CONTEXT" -n api-audit-lab get configmap sample
kubectl --context "$LAB_CONTEXT" -n api-audit-other get configmap sample
kubectl --context "$LAB_CONTEXT" get namespace api-audit-lab api-audit-other \
  -L experiments.kubernetes.io/fixture
# 両方が自分で作成した fixture であると確認してから:
kubectl --context "$LAB_CONTEXT" delete namespace api-audit-lab api-audit-other --wait=true --timeout=120s
kubectl --context "$LAB_CONTEXT" get namespace api-audit-lab api-audit-other
```

最後は両方 NotFound が期待値。削除が timeout したら完了扱いせず finalizer 等を private 記録で調べる。クラスタ削除は環境側の回収完了 gate 後に行う。policy は API リソースではなく namespace 削除で無効にならない。
