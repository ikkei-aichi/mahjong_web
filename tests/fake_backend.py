"""画面テスト用の、メモリ上だけで動く偽データ層。

Supabase に繋がずに views/*.py を実行するために、repo と auth を差し替える。
本物と同じシグネチャ・同じ戻り値の形を返すことだけを守り、
RLS や SQL の再現はしない（そこは SQL 側のテストとプリフライトで担保する）。
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timezone
from typing import Any

from mahjong.errors import AppError
from mahjong.rules import DEFAULT_RULESET, RuleSet
from mahjong.stats import RoundEntry

_ids = itertools.count(1)


def _uid(prefix: str) -> str:
    return f"{prefix}-{next(_ids):04d}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeBackend:
    """1グループ・1大会ぶんの最小構成を持つ偽バックエンド。"""

    def __init__(self, *, rules: RuleSet | None = None, provisional: bool = False):
        self.rules = rules or DEFAULT_RULESET
        self.group_id = _uid("grp")
        self.my_player_id = _uid("ply")
        self.tournament_id = _uid("trn")
        self.day_id = _uid("day")
        self.game_id = _uid("gam")

        self.group = {
            "group_id": self.group_id,
            "name": "テスト麻雀会",
            "description": None,
            "created_at": _now(),
            "my_player_id": self.my_player_id,
            "my_player_name": "わたし",
            "role": "owner",
            "is_provisional": provisional,
            "member_count": 4,
        }

        names = ["わたし", "たろう", "はなこ", "じろう"]
        self.players = [
            {
                "id": self.my_player_id if i == 0 else _uid("ply"),
                "name": name,
                "user_id": "user-1" if i == 0 else None,
                "role": "owner" if i == 0 else "member",
                "is_provisional": provisional if i == 0 else False,
                "deleted_at": None,
                "merged_into": None,
            }
            for i, name in enumerate(names)
        ]

        self.tournaments = [
            {
                "id": self.tournament_id,
                "group_id": self.group_id,
                "name": "2026年春麻雀大会",
                "ruleset": self.rules.to_dict(),
                "note": "テスト用",
                "created_by": "user-1",
                "created_at": _now(),
            }
        ]
        self.days = [
            {
                "id": self.day_id,
                "tournament_id": self.tournament_id,
                "group_id": self.group_id,
                "held_on": date(2026, 4, 5).isoformat(),
                "label": "初日",
                "note": None,
                "created_at": _now(),
            }
        ]
        self.games = [
            {
                "id": self.game_id,
                "name": "卓1",
                "created_at": _now(),
                "group_id": self.group_id,
                "tournament_id": self.tournament_id,
                "day_id": self.day_id,
                "held_on": self.days[0]["held_on"],
                "round_count": 0,
                "seats": [
                    {
                        "seat": i,
                        "player_id": p["id"],
                        "player_name": p["name"],
                        "user_id": p["user_id"],
                        "total_point": 0,
                    }
                    for i, p in enumerate(self.players[: self.rules.player_count])
                ],
            }
        ]
        self.rounds: list[dict[str, Any]] = []
        self.calls: list[str] = []

    # --- 記録 ---------------------------------------------------------

    def add_round(self, game_id, results, seat_to_player):
        self.calls.append("add_round")
        round_id = _uid("rnd")
        self.rounds.append(
            {
                "id": round_id,
                "game_id": game_id,
                "game_name": "卓1",
                "created_at": _now(),
                "ruleset": self.rules.to_dict(),
                "no": len(self.rounds) + 1,
                "results": [
                    {
                        "player_id": seat_to_player[r.seat],
                        "player_name": next(
                            s["player_name"]
                            for s in self.games[0]["seats"]
                            if s["seat"] == r.seat
                        ),
                        "seat": r.seat,
                        "raw_score": r.raw_score,
                        "point": r.point,
                        "rank": r.rank,
                        "kaze": r.kaze,
                        "tobi": r.tobi,
                    }
                    for r in sorted(results, key=lambda x: x.seat)
                ],
            }
        )
        self.games[0]["round_count"] = len(self.rounds)
        return round_id

    def update_round(self, round_id, results, seat_to_player):
        self.calls.append("update_round")
        for rnd in self.rounds:
            if rnd["id"] == round_id:
                rnd["results"] = [
                    {
                        "player_id": seat_to_player[r.seat],
                        "player_name": "",
                        "seat": r.seat,
                        "raw_score": r.raw_score,
                        "point": r.point,
                        "rank": r.rank,
                        "kaze": r.kaze,
                        "tobi": r.tobi,
                    }
                    for r in sorted(results, key=lambda x: x.seat)
                ]
                return
        raise AppError("半荘が見つかりません。")

    def delete_round(self, round_id):
        self.calls.append("delete_round")
        self.rounds = [r for r in self.rounds if r["id"] != round_id]
        for no, rnd in enumerate(self.rounds, start=1):
            rnd["no"] = no
        self.games[0]["round_count"] = len(self.rounds)

    def stored_for_recalc(self) -> list[dict[str, Any]]:
        return [
            {
                "round_id": rnd["id"],
                "seats": [r["seat"] for r in rnd["results"]],
                "raw_scores": [r["raw_score"] for r in rnd["results"]],
                "kazes": [r["kaze"] for r in rnd["results"]],
                "player_ids": [r["player_id"] for r in rnd["results"]],
            }
            for rnd in self.rounds
        ]

    def apply_recalculated(self, calculated) -> int:
        self.calls.append("apply_recalculated_rounds")
        for round_id, results, seat_to_player in calculated:
            self.update_round(round_id, results, seat_to_player)
        return len(calculated)

    def entries(self) -> list[RoundEntry]:
        return [
            RoundEntry(
                player_id=r["player_id"],
                rank=r["rank"],
                point=r["point"],
                tobi=r["tobi"],
                table_size=len(rnd["results"]),
            )
            for rnd in self.rounds
            for r in rnd["results"]
        ]


def install(monkeypatch, backend: FakeBackend) -> FakeBackend:
    """auth と repo を偽物に差し替える。"""
    import mahjong.auth as auth
    import mahjong.session as session
    from mahjong.repo import games, groups, queries, tournaments

    monkeypatch.setattr(auth, "require_login", lambda: {"id": "user-1", "email": "me@example.com"})
    monkeypatch.setattr(auth, "sidebar_account", lambda: None)

    monkeypatch.setattr(session, "my_groups", lambda: [backend.group])
    monkeypatch.setattr(groups, "list_my_groups", lambda: [backend.group])
    monkeypatch.setattr(groups, "get_group", lambda gid: backend.group)
    monkeypatch.setattr(
        groups, "list_players", lambda gid: [p for p in backend.players if not p["deleted_at"]]
    )
    monkeypatch.setattr(groups, "list_all_players", lambda gid: backend.players)
    monkeypatch.setattr(
        groups, "player_names", lambda gid: {p["id"]: p["name"] for p in backend.players}
    )
    monkeypatch.setattr(groups, "list_invites", lambda gid: [])

    monkeypatch.setattr(tournaments, "list_tournaments", lambda gid: backend.tournaments)
    monkeypatch.setattr(
        tournaments,
        "get_tournament",
        lambda tid: next((t for t in backend.tournaments if t["id"] == tid), None),
    )
    monkeypatch.setattr(tournaments, "get_ruleset", lambda tid: (backend.rules, []))
    monkeypatch.setattr(tournaments, "list_days", lambda tid: backend.days)
    monkeypatch.setattr(
        tournaments, "get_day", lambda did: next((d for d in backend.days if d["id"] == did), None)
    )

    monkeypatch.setattr(games, "list_games", lambda did: backend.games)
    monkeypatch.setattr(games, "list_games_in_tournament", lambda tid: backend.games)
    monkeypatch.setattr(
        games, "get_game", lambda gid: next((g for g in backend.games if g["id"] == gid), None)
    )
    monkeypatch.setattr(games, "list_rounds", lambda gid: backend.rounds)
    monkeypatch.setattr(games, "add_round", backend.add_round)
    monkeypatch.setattr(games, "update_round", backend.update_round)
    monkeypatch.setattr(games, "delete_round", backend.delete_round)

    monkeypatch.setattr(queries, "fetch_entries", lambda scope, value: backend.entries())
    # 本物の queries._entry と同じ形にすること。table_size / kaze を落とすと
    # 風別成績や連続記録の不具合がテストをすり抜ける。
    monkeypatch.setattr(
        queries,
        "fetch_rounds_in_order",
        lambda scope, value: [
            [
                RoundEntry(
                    player_id=r["player_id"],
                    rank=r["rank"],
                    point=r["point"],
                    tobi=r["tobi"],
                    table_size=len(rnd["results"]),
                    kaze=r["kaze"],
                )
                for r in rnd["results"]
            ]
            for rnd in backend.rounds
        ],
    )
    monkeypatch.setattr(queries, "count_rounds", lambda scope, value: len(backend.rounds))
    monkeypatch.setattr(
        queries, "fetch_stored_rounds_for_recalc", lambda tid: backend.stored_for_recalc()
    )
    monkeypatch.setattr(
        queries,
        "apply_recalculated_rounds",
        lambda tid, rules, calculated: backend.apply_recalculated(calculated),
    )
    monkeypatch.setattr(
        tournaments,
        "update_tournament",
        lambda tid, **kwargs: backend.calls.append("update_tournament"),
    )
    return backend
