"""Deterministic factual editorial, without a model/API or invented specifications."""
from datetime import time
from pathlib import Path
from pipeline_common import JST, PipelineError, load_json, timestamp
from publication_pipeline import validate_collection, validate_daily, week_label


def require_windows_owner(root):
    value = load_json(root/'config/automation-owner.json')
    if value != {'schemaVersion': 1, 'editorialOwner': 'windows'}:
        raise PipelineError('WINDOWS_EDITORIAL_OWNERSHIP_REQUIRED')


def deals(products):
    seen = set()
    result = []
    for category in products['categories']:
        for item in category['items']:
            if item.get('isDeal') is True and item['code'] not in seen:
                result.append(item['code'])
                seen.add(item['code'])
    return result[:5]


def daily(root: Path, now):
    require_windows_owner(root)
    products, _, status, queries = validate_collection(root, now)
    if now.astimezone(JST).time() < time(6, 30):
        raise PipelineError('BEFORE_EDITORIAL_WINDOW')
    return {'generatedAt': now.isoformat(), 'sourceProductsSha256': status['productsSha256'],
        'dailyHeadline': 'ガジェットの価格比較と価格履歴',
        'dailySummary': '掲載商品の価格・レビュー件数・送料条件を比較できます。'
            '値下がり候補は保存済みの価格履歴に対する判定で、市場全体の最安値を示すものではありません。',
        'featuredDeals': [{'code': code, 'reason': '保存済みの価格履歴に対する値下がり候補です。販売条件を確認してください。'} for code in deals(products)],
        'categoryNotes': [{'categoryId': q['id'], 'topPickCode': '',
            'summary': q['name']+'の掲載価格・レビュー件数・送料条件を比較できます。',
            'watchouts': ['送料やポイント条件は販売ページで確認してください。',
                '保存価格との比較であり、市場全体の最安値ではありません。']} for q in queries]}


def weekly(root: Path, now, *, max_collection_age_hours: int = 3):
    require_windows_owner(root)
    editorial = validate_daily(root, now, max_age_hours=max_collection_age_hours)
    if now.astimezone(JST).weekday() != 6 or now.astimezone(JST).time() < time(6, 40):
        raise PipelineError('BEFORE_WEEKLY_EDITORIAL_WINDOW')
    if timestamp(editorial['generatedAt']) > now:
        raise PipelineError('EDITORIAL_GENERATION_MISMATCH')
    products, _, status, _ = validate_collection(root, now, max_age_hours=max_collection_age_hours)
    return {'generatedAt': now.isoformat(), 'sourceProductsSha256': status['productsSha256'],
        'weekLabel': week_label(now.astimezone(JST).date()),
        'title': 'ガジェット価格ウォッチ：掲載価格と価格履歴を確認',
        'intro': '掲載商品の価格と保存済みの価格履歴を整理しました。販売条件は各販売ページで確認してください。',
        'featured': [{'code': code, 'comment': '保存済みの価格履歴に対する値下がり候補です。'} for code in deals(products)],
        'points': ['価格比較は掲載商品の取得時点のデータに基づきます。',
            '保存価格との比較であり、市場全体の最安値を保証しません。'],
        'closing': '送料・ポイント・在庫などの販売条件を確認して比較してください。',
        'hashtags': ['ガジェット', '価格比較', '価格ウォッチ']}
