# テスト用フィクスチャ

`vc_transaction_documented_shape.json` は **実測データではない。**
バリューコマース公式技術資料（注文別レポートAPI v3 リファレンス）に
記載されたフィールド名と入れ子構造から組み立てたもので、パーサが
「資料どおりの形」を処理できることだけを担保する。

以下は資料に明示が無く、実レスポンスで確認するまで確定しない。

- `orderDate` / `clickDate` / `approvalDate` / `updDate` の日時書式
  （ここでは `responseTime` と同じ `YYYY-MM-DD HH:MM:SS` を仮に置いている）
- `approvalStatus` の大文字・小文字
- `vcptn` / `affiliateSite` が空のときの表現（`null` か空文字か `"unknown"` か）

初回の本番実行後、`storage/raw/valuecommerce/{日付}/` に保存された原本を
匿名化してここへ差し替え、上記を確定させること。
