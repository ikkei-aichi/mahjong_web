"""麻雀管理アプリの中核ロジック。

`rules` / `scoring` / `stats` は DB にも Streamlit にも依存しない純粋モジュールで、
pytest から直接テストできる。DB アクセスは `db` / `repo` に閉じ込める。
"""
