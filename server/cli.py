#!/usr/bin/env python3

import argparse
import asyncio
import json
import sys

# Ensure local imports work when run from project root
if 'server' not in sys.path:
    sys.path.append('server')

from _v3_analyzer_jupiter import (
    refresh_missing_jupiter_data,
    refresh_until_three,
)
from _v3_tokens_reader import TokensReaderV3
from _v3_chart_data_reader import ChartDataReaderV3
from _v2_balance import BalanceV1
from _v3_new_tokens import get_scanner as get_jupiter_scanner
from _v3_analyzer_jupiter import get_analyzer as get_jupiter_analyzer
from _v3_db_pool import get_db_pool
from _v2_sol_price import SolPriceMonitor
from tools.dex_series import build_dex_like_price_series, build_our_trade_series
from ai.tools.migrate import main as ai_migrate_main
from ai.tools.seed_patterns import seed as ai_seed_patterns
from ai.infer.forecast_loop import main_loop as ai_forecast_loop
from ai.infer.tcn_forecast import tcn_forecast_loop as ai_tcn_forecast_loop
from ai.patterns.importer import import_file as ai_import_patterns
from _v3_author_activity import AuthorActivityRecorder


async def cmd_refresh_jupiter(args: argparse.Namespace):
    if args.until_three:
        res = await refresh_until_three(
            debug=not args.quiet,
            batch_size=args.batch_size,
            delay_seconds=args.delay,
            max_rounds=args.max_rounds,
        )
    else:
        res = await refresh_missing_jupiter_data(
            debug=not args.quiet,
            batch_size=args.batch_size,
            delay_seconds=args.delay,
            force_rescan=args.force_rescan,
        )
    print("\n=== RESULT ===")
    print(json.dumps(res, ensure_ascii=False, indent=2))


async def cmd_timers(args: argparse.Namespace):
    balance = BalanceV1()
    tokens = TokensReaderV3(debug=not args.quiet)
    charts = ChartDataReaderV3(debug=not args.quiet)

    if args.action == 'start':
        await balance.__aenter__()
        await balance.load_balance_data()
        await balance.start_auto_refresh()
        await tokens.start_auto_refresh()
        await charts.start_auto_refresh()
        print("✅ Timers started: balance, tokens, charts")
    elif args.action == 'stop':
        await balance.stop_auto_refresh()
        await tokens.stop_auto_refresh()
        await charts.stop_auto_refresh()
        print("🛑 Timers stopped: balance, tokens, charts")
    else:  # status
        status = {
            'balance': balance.get_status(),
            'tokens': tokens.get_status(),
            'charts': {
                'is_running': charts.is_running,
                'connected_clients': len(charts.connected_clients),
            },
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))


async def cmd_scanner(args: argparse.Namespace):
    scanner = await get_jupiter_scanner()
    if args.action == 'start':
        res = await scanner.start_auto_scan()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.action == 'stop':
        res = await scanner.stop_auto_scan()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(scanner.get_status(), ensure_ascii=False, indent=2))


async def cmd_analyzer(args: argparse.Namespace):
    analyzer = await get_jupiter_analyzer()
    if args.action == 'start':
        res = await analyzer.start()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.action == 'stop':
        res = await analyzer.stop()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({'is_scanning': analyzer.is_scanning}, ensure_ascii=False, indent=2))


async def cmd_db(args: argparse.Namespace):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if args.action == 'reset':
            # Danger: truncates all main tables
            confirm = (args.confirm or '').lower().strip()
            if confirm != 'yes':
                print(json.dumps({"success": False, "error": "confirm with --confirm yes"}, ensure_ascii=False, indent=2))
                return
            await conn.execute('TRUNCATE TABLE trades RESTART IDENTITY CASCADE')
            await conn.execute('TRUNCATE TABLE token_metrics_seconds RESTART IDENTITY')
            await conn.execute('TRUNCATE TABLE tokens RESTART IDENTITY CASCADE')
            print(json.dumps({"success": True, "message": "database truncated"}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='Crypto App CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # refresh-jupiter
    p_ref = subparsers.add_parser('refresh-jupiter', help='Refresh Jupiter data for tokens')
    p_ref.add_argument('--until-three', action='store_true', help='Repeat rounds until all tokens have token_pair')
    p_ref.add_argument('--force-rescan', action='store_true', help='Force rescan all tokens')
    p_ref.add_argument('--batch-size', type=int, default=100, help='Batch size (<=100)')
    p_ref.add_argument('--delay', type=float, default=3.0, help='Delay between batches/rounds')
    p_ref.add_argument('--max-rounds', type=int, default=3, help='Max rounds for --until-three')
    p_ref.add_argument('--quiet', action='store_true', help='Less verbose output')
    p_ref.set_defaults(func=cmd_refresh_jupiter)

    # timers
    p_tim = subparsers.add_parser('timers', help='Control backend timers (balance, tokens, charts)')
    p_tim.add_argument('action', choices=['start', 'stop', 'status'])
    p_tim.add_argument('--quiet', action='store_true')
    p_tim.set_defaults(func=cmd_timers)

    # scanner
    p_scn = subparsers.add_parser('scanner', help='Control new tokens scanner')
    p_scn.add_argument('action', choices=['start', 'stop', 'status'])
    p_scn.set_defaults(func=cmd_scanner)

    # analyzer
    p_an = subparsers.add_parser('analyzer', help='Control Jupiter analyzer')
    p_an.add_argument('action', choices=['start', 'stop', 'status'])
    p_an.set_defaults(func=cmd_analyzer)

    # db
    p_db = subparsers.add_parser('db', help='DB maintenance helpers')
    sp_db = p_db.add_subparsers(dest='action', required=True)

    p_reset = sp_db.add_parser('reset', help='TRUNCATE tokens/trades/token_metrics_seconds (DANGER)')
    p_reset.add_argument('--confirm', type=str, help='Must be "yes" to proceed')
    p_reset.set_defaults(func=cmd_db)

    # sol-price (one-shot fetch and print current SOL/USD from DexScreener with Jupiter fallback)
    async def cmd_sol_price(args: argparse.Namespace):
        mon = SolPriceMonitor(update_interval=1, debug=False)
        try:
            await mon.ensure_session()
            price = await mon._fetch_sol_price()  # one-shot fetch
            print(f"{price}")
        finally:
            await mon.close()

    p_sp = subparsers.add_parser('sol-price', help='Print current SOL/USD price once')
    p_sp.set_defaults(func=cmd_sol_price)

    # compare-series: dump OUR (trades-based) and DEX-like series for a token
    async def cmd_compare_series(args: argparse.Namespace):
        token_id = int(args.token_id)
        dex = await build_dex_like_price_series(token_id, debug=not args.quiet)
        if dex:
            start_ts, end_ts = dex[0]['ts'], dex[-1]['ts']
        else:
            start_ts, end_ts = 0, 0
        ours = await build_our_trade_series(token_id, start_ts, end_ts, debug=not args.quiet)
        out = {
            'token_id': token_id,
            'dex_like_points': len(dex),
            'our_points': len(ours),
            'dex_like': dex[-args.limit:] if args.limit > 0 else dex,
            'our': ours[-args.limit:] if args.limit > 0 else ours,
        }
        print(json.dumps(out, ensure_ascii=False))

    p_cmp = subparsers.add_parser('compare-series', help='Compare OUR vs Dex-like USD/second series for token')
    p_cmp.add_argument('--token-id', type=int, required=True)
    p_cmp.add_argument('--limit', type=int, default=120, help='Tail points to print (0=all)')
    p_cmp.add_argument('--quiet', action='store_true')
    p_cmp.set_defaults(func=cmd_compare_series)

    async def cmd_author_activity(args: argparse.Namespace):
        recorder = AuthorActivityRecorder(debug=not args.quiet)
        try:
            if args.action == 'fetch':
                result = await recorder.fetch_and_store(
                    author_wallet=args.wallet,
                    limit=args.limit,
                    before=args.before,
                    include_incoming=args.include_incoming,
                )
            else:
                result = {
                    "success": True,
                    "candidates": await recorder.get_watch_candidates(
                        author_wallet=args.wallet,
                        limit=args.limit,
                        only_without_mint=not args.include_with_mint,
                    ),
                }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            await recorder.close()

    # author-activity
    p_author = subparsers.add_parser('author-activity', help='Track author wallets and transfers via Helius')
    sp_author = p_author.add_subparsers(dest='action', required=True)

    p_author_fetch = sp_author.add_parser('fetch', help='Fetch transfers for an author wallet and store in DB')
    p_author_fetch.add_argument('--wallet', required=True, help='Author wallet address')
    p_author_fetch.add_argument('--limit', type=int, default=100, help='Max transactions to fetch (<= HISTORY_HELIUS_LIMIT)')
    p_author_fetch.add_argument('--before', type=str, help='Pagination signature (Helius before param)')
    p_author_fetch.add_argument('--include-incoming', action='store_true', help='Also store transactions where author receives funds')
    p_author_fetch.add_argument('--quiet', action='store_true')
    p_author_fetch.set_defaults(func=cmd_author_activity)

    p_author_candidates = sp_author.add_parser('candidates', help='List stored target wallets to watch')
    p_author_candidates.add_argument('--wallet', type=str, help='Filter by author wallet')
    p_author_candidates.add_argument('--limit', type=int, default=20, help='Rows to return')
    p_author_candidates.add_argument('--include-with-mint', action='store_true', help='Include rows where token_mint already known')
    p_author_candidates.add_argument('--quiet', action='store_true')
    p_author_candidates.set_defaults(func=cmd_author_activity)

    # AI utilities
    async def cmd_ai(args: argparse.Namespace):
        action = args.action
        if action == 'migrate':
            await ai_migrate_main()
            print('{"success": true, "action": "migrate"}')
        elif action == 'seed-patterns':
            await ai_seed_patterns()
            print('{"success": true, "action": "seed-patterns"}')
        elif action == 'forecast-loop':
            print('{"success": true, "action": "forecast-loop", "note": "press Ctrl+C to stop"}')
            await ai_forecast_loop()
        elif action == 'tcn-forecast-loop':
            print('{"success": true, "action": "tcn-forecast-loop", "note": "runs TCN predictor; press Ctrl+C to stop"}')
            await ai_tcn_forecast_loop()
        elif action == 'import-patterns':
            res = await ai_import_patterns(args.file, args.source)
            import json as _json
            print(_json.dumps(res, ensure_ascii=False))

    p_ai = subparsers.add_parser('ai', help='AI utilities')
    sp_ai = p_ai.add_subparsers(dest='action', required=True)

    p_ai_mig = sp_ai.add_parser('migrate', help='Apply AI SQL migrations')
    p_ai_mig.set_defaults(func=cmd_ai)

    p_ai_seed = sp_ai.add_parser('seed-patterns', help='Seed ai_patterns dictionary')
    p_ai_seed.set_defaults(func=cmd_ai)

    p_ai_fore = sp_ai.add_parser('forecast-loop', help='Run baseline forecast loop (1 Hz)')
    p_ai_fore.set_defaults(func=cmd_ai)

    p_ai_tcn = sp_ai.add_parser('tcn-forecast-loop', help='Run TCN forecast loop (prints predictions)')
    p_ai_tcn.set_defaults(func=cmd_ai)

    p_ai_imp = sp_ai.add_parser('import-patterns', help='Import manual token patterns from CSV')
    p_ai_imp.add_argument('--file', required=True)
    p_ai_imp.add_argument('--source', default='manual')
    p_ai_imp.set_defaults(func=cmd_ai)



    args = parser.parse_args()
    # Some AI helpers might not take args; adapt
    if hasattr(args, 'func'):
        asyncio.run(args.func(args))
    else:
        # Legacy safeguard
        pass


if __name__ == '__main__':
    main()
