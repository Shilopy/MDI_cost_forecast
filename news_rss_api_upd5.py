# news_rss_api_upd5.py - Финальная версия анализатора новостей MDI

import feedparser
import requests
import json
import time
from datetime import datetime, timedelta, timezone
import re
from bs4 import BeautifulSoup
import asyncio
import aiohttp
from dotenv import load_dotenv
import os
import numpy as np
# import pandas as pd # Убрано, так как не используется напрямую
from textblob import TextBlob
import warnings
import urllib3
warnings.filterwarnings('ignore')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) # Отключить предупреждения SSL

# --- ЖЕСТКО ЗАДАННЫЙ КЛЮЧ API (для гарантии работы) ---
# В реальной системе его следует хранить в секрете, например, в .env
HARDCODED_NEWSAPI_KEY = 'f7e7ee9e68c64bebab1f6447c9ecd67b'
# ----------------------------------------------------

# Попытка загрузить переменные окружения из .env
load_dotenv()

class MDIAnalyzer:
    def __init__(self):
        self.mdi_components = {
            'PM200': {
                'components': {
                    '4,4-mdi': 0.775, '2,2-mdi': 0.02, '2,4-mdi': 0.035,
                    'oligomers': 0.125, 'ureidone': 0.015, 'carbodiimide': 0.0075,
                    'alkyl_substituted': 0.003, 'other_isocyanates': 0.002,
                    'impurities': 0.004, 'organic_impurities': 0.0035
                },
                'region': 'China',
                'producer': 'Wanhua',
                'type': 'Polymeric MDI'
            },
            '44V20L': {
                'components': {
                    '4,4-mdi': 0.97, '2,2-mdi': 0.0075, '2,4-mdi': 0.015,
                    'oligomers': 0.015, 'ureidone': 0.0075, 'carbodiimide': 0.003,
                    'alkyl_substituted': 0.002, 'other_isocyanates': 0.0015,
                    'impurities': 0.0005, 'organic_impurities': 0.002
                },
                # Уточнение: 44V20L производится в Китае и закупается только в Китае
                'region': 'China',
                'producer': 'Covestro',
                'type': 'Pure MDI'
            }
        }
        
        self.raw_materials = {
            'aniline': 0.8, 'phosgene': 0.7, 'benzene': 0.6, 'gas': 0.5, 'chlorine': 0.4
        }

    def analyze_sentiment(self, text):
        """Анализ настроения текста"""
        try:
            blob = TextBlob(text)
            polarity = blob.sentiment.polarity
            return polarity
        except Exception as e:
            return 0.0

class NewsIntegrator:
    def __init__(self):
        # --- Обновленный список RSS-каналов ---
        self.rss_feeds = {
            'chemical_industry_global': [
                'https://www.icis.com/news/rss/chemicals/', # Проверить
                # Добавить другие глобальные химические фиды, если найдутся
                'https://www.chemicalweek.com/feeds/home', # Попробуем снова
            ],
            'energy_raw_materials': [
                'https://oilprice.com/rss/main',
            ],
            'specialized_chemical': [
                # 'https://www.petrochemicalupdate.com/feed/', # Timeout ранее
            ]
            # 'russian_news': [...] убран, фокус на Китае
        }
        
        # --- ЛОГИКА ПОЛУЧЕНИЯ КЛЮЧА API ---
        # 1. Попробуем получить из переменной окружения
        self.newsapi_key = os.getenv('NEWSAPI_KEY')
        # 2. Если не найден, используем жестко заданный ключ
        if not self.newsapi_key:
            self.newsapi_key = HARDCODED_NEWSAPI_KEY
            print("⚠️  Ключ NewsAPI не найден в переменных окружения. Используется жестко заданный ключ.")
        else:
            print("✅ Ключ NewsAPI успешно загружен из переменных окружения.")
        # ----------------------------------
        
        # --- Обновленные ключевые слова ---
        self.keywords = {
            'high_impact': [
                'форс-мажор', 'force majeure', 'plant shutdown', 'production halt', 'emergency stop', '紧急停车', '停产',
                'maintenance', 'turnaround', '检修', '大修', 'outage', 'startup', 'запуск', '启动'
            ],
            'medium_impact': [
                'цены на сырье', 'рост цен', 'дефицит', 'поставки', 'экспорт',
                'raw material prices', 'supply shortage', 'export restrictions',
                '原料价格', '供应紧张', '出口限制', '产能', '产量'
            ],
            'low_impact': [
                'прогноз', 'анализ', 'рынок', 'тенденции', 'market analysis',
                'forecast', 'trends', 'market conditions',
                '市场分析', '趋势', '预测'
            ],
            'logistics': [
                'транспортировка', 'доставка', 'логистика', 'порт', 'ж/д',
                'shipping', 'logistics', 'transportation', 'railway', 'port',
                '物流', '运输', '港口', '铁路'
            ],
            'regional': {
                'China': ['Китай', 'Wanhua', 'PM200', '44V20L', 'Шанхай', 'Циндао', '万华化学', '烟台', '宁波', '福建', '科思创', 'Covestro China', '亨斯迈', 'Huntsman'],
                'Asia': ['Азия', '亚洲', '亚太'],
            },
            # --- Обновленные основные ключевые слова ---
            'main_keywords': [
                # Производители и марки - фокус на китайское производство
                'mdi', 'mdipm200', '44v20l', 'wanhua', '万华化学', 'covestro', '科思创', 'huntsman', '亨斯迈', 'basf', '巴斯夫',
                'methylenediphenyl diisocyanate', 'полимерный mdi', 'чистый mdi', 'supranal', 'isonate',
                '聚氨酯', '二苯基甲烷二异氰酸酯', 'mdi polymer', 'mdi pure',
                # Сырье
                'aniline', 'phosgene', 'benzene', 'gas', 'хлор', 'анилин', 'фосген', 'бензол', 'газ',
                '氯气', '苯', '光气', '天然气', 'natural gas', 'crude benzene', '硝基苯', 'nitrobenzene',
                # Производство и поставки - фокус на Китай
                'production', 'supply', 'capacity', 'outage', 'output', 'plant', 'facility', 'unit',
                'производство', 'поставка', 'мощность', 'отключение', 'выпуск', 'завод', 'установка',
                '生产', '供应', '产能', '停车', '产量', '装置', '工厂', '单元',
                # Специфические события
                'force majeure', 'maintenance', 'shutdown', 'startup', 'expansion', 'debottlenecking', 'turnaround', 'revamp',
                'форс-мажор', 'ремонт', 'остановка', 'запуск', 'расширение', 'модернизация',
                '紧急停车', '检修', '扩产', '新产能', '技改', '大修', '开车', '投产',
                # Торговля и цены
                'price', 'pricing', 'contract price', 'spot price', 'export', 'import', 'trade',
                'цена', 'контрактная цена', 'спотовая цена', 'экспорт', 'импорт', 'торговля',
                '价格', '合同价', '现货价', '出口', '进口', '贸易',
                # Логистика и регионы Китая
                'logistics', 'shipment', 'delivery', 'port', 'transportation',
                'логистика', 'отгрузка', 'доставка', 'порт', 'транспортировка',
                '物流', '装运', '交付', '港口', '运输',
                'Шанхай', 'Циндао', 'Нанкин', 'Тяньцзинь', '宁波', '烟台', '上海', '青岛', '南京', '天津'
            ]
        }
        
        # Список ключевых слов для фильтрации
        self.mdi_keywords = list(set(
            self.get_all_keywords()
        ))
        print(f"🔍 Используется {len(self.mdi_keywords)} уникальных ключевых слов для поиска.")

    def get_all_keywords(self):
        """Получить полный список ключевых слов"""
        keywords = []
        # Основные ключевые слова
        keywords.extend(self.keywords['main_keywords'])
        # Региональные ключевые слова
        for region_keywords in self.keywords['regional'].items():
            # region_keywords это tuple (ключ, значение)
            # Нам нужно только значение (список слов)
            if isinstance(region_keywords, tuple):
                keywords.extend(region_keywords[1])
            else:
                 # Если это просто список (ошибка в предыдущем коде)
                 keywords.extend(region_keywords)
        # Ключевые слова сырья
        keywords.extend(['aniline', 'phosgene', 'benzene', 'gas', 'хлор', 'анилин', 'фосген', 'бензол', 'газ', '氯气', '苯', '光气', '天然气', 'natural gas', 'crude benzene', '硝基苯'])
        # Ключевые слова воздействия
        for impact_keywords in [self.keywords['high_impact'], self.keywords['medium_impact'], self.keywords['low_impact'], self.keywords['logistics']]:
            keywords.extend(impact_keywords)
        return keywords

    async def fetch_rss_news(self):
        """Получение новостей из RSS фидов"""
        all_news = []
        total_feeds = sum(len(feeds) for feeds in self.rss_feeds.values())
        if total_feeds > 0:
            print(f"📡 Начинаем сбор новостей из {total_feeds} RSS фидов...")
        else:
            print("📡 Нет активных RSS фидов для сбора.")
            return all_news
        
        for category, feeds in self.rss_feeds.items():
            if not feeds:
                continue
            print(f"   📂 Категория: {category} ({len(feeds)} фидов)")
            for feed_url in feeds:
                try:
                    print(f"      🔍 Запрашиваем: {feed_url}")
                    # Увеличиваем таймаут и отключаем проверку SSL
                    response = requests.get(feed_url, timeout=15, verify=False)
                    if response.status_code == 200:
                        feed = feedparser.parse(response.content)
                        entries_count = len(feed.entries)
                        print(f"      ✅ Получено {entries_count} записей")
                        for entry in feed.entries[:20]: # Увеличиваем лимит до 20
                            pub_date = None
                            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                try:
                                    pub_date = datetime(*entry.published_parsed[:6])
                                    # Приводим к aware datetime (UTC)
                                    if pub_date.tzinfo is None:
                                        pub_date = pub_date.replace(tzinfo=timezone.utc)
                                except Exception as e:
                                    pub_date = datetime.now(timezone.utc) # Используем текущую дату
                            else:
                                pub_date = datetime.now(timezone.utc) # Используем текущую дату
                            
                            news_item = {
                                'title': getattr(entry, 'title', ''),
                                'content': getattr(entry, 'summary', ''),
                                'date': pub_date,
                                'source': getattr(feed.feed, 'title', 'RSS Feed'),
                                'url': getattr(entry, 'link', ''),
                                'category': category
                            }
                            all_news.append(news_item)
                    else:
                        print(f"      ❌ Ошибка HTTP {response.status_code} для {feed_url}")
                except Exception as e:
                    print(f"      ⚠️ Ошибка при парсинге RSS {feed_url}: {type(e).__name__}")
                    continue # Продолжаем со следующим фидом
        
        print(f"   📥 Всего из RSS: {len(all_news)} новостей")
        return all_news

    async def fetch_newsapi_news(self):
        """Получение новостей через NewsAPI с использованием специфических запросов"""
        news = []
        print("📰 Начинаем сбор новостей через NewsAPI с фильтрацией...")
        
        if not self.newsapi_key or self.newsapi_key == 'demo':
            print("   ⚠️ NewsAPI ключ не найден или в демо-режиме")
            return news
            
        try:
            from_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime('%Y-%m-%d') # Увеличим период
            
            # 1. Запрос новостей, специфичных для MDI/Wanhua/Covestro China
            print("   🔍 Запрос 1: Новости MDI/Wanhua/Covestro China за последние 5 дней")
            search_terms_mdi = [
                'MDI', 'Wanhua', '万华化学', 'Covestro', '科思创', '44V20L', 'PM200',
                '聚氨酯', '二苯基甲烷二异氰酸酯', 'Huntsman', '亨斯迈'
            ]
            search_query_mdi = ' OR '.join(search_terms_mdi)
            
            url_mdi = "https://newsapi.org/v2/everything"
            params_mdi = {
                'q': search_query_mdi,
                'from': from_date,
                'sortBy': 'publishedAt',
                'pageSize': 40, # Увеличим
                'page': 1,
                'apiKey': self.newsapi_key
            }
            
            response_mdi = requests.get(url_mdi, params=params_mdi, timeout=20)
            if response_mdi.status_code == 200:
                data_mdi = response_mdi.json()
                articles_fetched_mdi = len(data_mdi.get('articles', []))
                print(f"      ✅ Получено {articles_fetched_mdi} новостей по MDI/производителям")
                for article in data_mdi.get('articles', []):
                    try:
                        pub_date_str = article['publishedAt']
                        if pub_date_str.endswith('Z'):
                            pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                        else:
                            pub_date = datetime.fromisoformat(pub_date_str)
                        
                        news.append({
                            'title': article['title'],
                            'content': article['description'] or '',
                            'date': pub_date,
                            'source': article['source']['name'],
                            'url': article['url'],
                            'category': 'newsapi_mdi_specific'
                        })
                    except Exception as e:
                        continue
            else:
                print(f"      ❌ NewsAPI ошибка (MDI запрос): {response_mdi.status_code} - {response_mdi.text}")

            # 2. Запрос новостей о сырье (аналин, бензол, газ) - фокус на Китай
            print("   🔍 Запрос 2: Новости о сырье (аналин, бензол, газ) с фокусом на Китай")
            search_terms_raw = [
                'aniline', 'benzene', 'natural gas', '氯气', '苯', '天然气', 'crude benzene', '硝基苯'
            ]
            search_query_raw = ' OR '.join(search_terms_raw)
            search_query_raw_with_region = f"({search_query_raw}) AND (China OR 中国 OR Китай)"
            
            url_raw = "https://newsapi.org/v2/everything"
            params_raw = {
                'q': search_query_raw_with_region,
                'from': from_date,
                'sortBy': 'publishedAt',
                'pageSize': 30, # Увеличим
                'page': 1,
                'apiKey': self.newsapi_key
            }
            
            response_raw = requests.get(url_raw, params=params_raw, timeout=20)
            if response_raw.status_code == 200:
                data_raw = response_raw.json()
                articles_fetched_raw = len(data_raw.get('articles', []))
                print(f"      ✅ Получено {articles_fetched_raw} новостей о сырье (с региональным фокусом)")
                for article in data_raw.get('articles', []):
                    try:
                        pub_date_str = article['publishedAt']
                        if pub_date_str.endswith('Z'):
                            pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                        else:
                            pub_date = datetime.fromisoformat(pub_date_str)
                        
                        news.append({
                            'title': article['title'],
                            'content': article['description'] or '',
                            'date': pub_date,
                            'source': article['source']['name'],
                            'url': article['url'],
                            'category': 'newsapi_raw_materials'
                        })
                    except Exception as e:
                        continue
            else:
                print(f"      ❌ NewsAPI ошибка (сырье запрос): {response_raw.status_code} - {response_raw.text}")

            # 3. Запрос новостей с ключевых химических сайтов
            print("   🔍 Запрос 3: Новости с ключевых химических сайтов (за последние 5 дней)")
            # --- Обновленный список доменов ---
            domains = "icis.com,platts.com,chemanalyst.com,chemicalweek.com,hydrocarbonprocessing.com,polymerupdate.com"
            url_domains = "https://newsapi.org/v2/everything"
            params_domains = {
                'domains': domains,
                'from': from_date,
                'sortBy': 'publishedAt',
                'pageSize': 30, # Увеличим
                'page': 1,
                'apiKey': self.newsapi_key
            }
            
            response_domains = requests.get(url_domains, params=params_domains, timeout=20)
            if response_domains.status_code == 200:
                data_domains = response_domains.json()
                articles_fetched_domains = len(data_domains.get('articles', []))
                print(f"      ✅ Получено {articles_fetched_domains} новостей с химических сайтов")
                for article in data_domains.get('articles', []):
                    try:
                        pub_date_str = article['publishedAt']
                        if pub_date_str.endswith('Z'):
                            pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                        else:
                            pub_date = datetime.fromisoformat(pub_date_str)
                        
                        # Проверяем, релевантно ли для MDI (простая фильтрация)
                        title_content = (article['title'] or '') + ' ' + (article['description'] or '')
                        if any(kw in title_content.lower() for kw in ['mdi', 'polyurethane', 'diisocyanate', 'wanhua', '万华', 'covestro', '科思创', 'huntsman', '亨斯迈']):
                            news.append({
                                'title': article['title'],
                                'content': article['description'] or '',
                                'date': pub_date,
                                'source': article['source']['name'],
                                'url': article['url'],
                                'category': 'newsapi_chemical_sites'
                            })
                    except Exception as e:
                        continue
            else:
                print(f"      ❌ NewsAPI ошибка (домены запрос): {response_domains.status_code} - {response_domains.text}")

        except Exception as e:
            print(f"   ⚠️ Ошибка при получении новостей NewsAPI: {type(e).__name__}")
        
        unique_news = list({v['url']:v for v in news}.values()) # Удаление дубликатов по URL
        print(f"   📥 Всего уникальных новостей из NewsAPI: {len(unique_news)}")
        return unique_news

    async def fetch_chinese_news(self):
        """Получение новостей с китайских сайтов (простой парсинг)"""
        chinese_news = []
        print("🇨🇳 Начинаем сбор новостей с китайских сайтов...")
        
        # Попытка извлечь релевантную информацию из pudaily (как в предыдущем примере)
        # pudaily.com похоже не отдает контент напрямую через requests (возвращает пустой body)
        # Попробуем chem99, который требует авторизации, но может быть есть публичные новости
        try:
            # Пример: попробуем получить главную страницу chem99
            site_url = 'https://www.chem99.com/'
            print(f"   🔍 Парсим {site_url} (вручную)")
            async with aiohttp.ClientSession() as session:
                async with session.get(site_url, timeout=15, ssl=False) as response:
                    if response.status == 200:
                        html = await response.text()
                        # Проверим, есть ли упоминания MDI или производителей
                        # Сайт chem99 требует авторизации, скорее всего, мы получим страницу входа
                        # Но если вдруг есть публичный контент...
                        if 'mdi' in html.lower() or '万华' in html or '科思创' in html:
                            title = "Потенциальное упоминание MDI на Chem99 (требует авторизации для полного доступа)"
                            content = "Главная страница Chem99 содержит ключевые слова, связанные с MDI. Для полного анализа требуется авторизация."
                            pub_date = datetime.now(timezone.utc)
                            
                            chinese_news.append({
                                'title': title,
                                'content': content,
                                'date': pub_date,
                                'source': 'Chem99 (Manual Check)',
                                'url': site_url,
                                'category': 'chinese_news_manual'
                            })
                            print(f"      ⚠️ Найдено потенциальное упоминание на {site_url}")
                        else:
                            print(f"      ℹ️  Ключевые слова не найдены на {site_url}")
                    else:
                        print(f"      ❌ Ошибка HTTP {response.status} для {site_url}")
        except Exception as e:
            print(f"      ⚠️ Ошибка при парсинге {site_url}: {type(e).__name__}")
            
        print(f"   📥 Всего с китайских сайтов: {len(chinese_news)} новостей")
        return chinese_news

    def is_relevant_to_mdi(self, text):
        """Проверка релевантности текста теме MDI"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in self.mdi_keywords)

    async def fetch_all_news_sources(self):
        """Получение новостей из всех источников"""
        print("📡 Начинаем сбор новостей из всех источников...")
        
        tasks = [
            self.fetch_rss_news(),
            self.fetch_newsapi_news(), # Обновленная функция
            self.fetch_chinese_news()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_news = []
        source_names = ['RSS', 'NewsAPI', 'Chinese Sites']
        active_sources = 0
        for i, result in enumerate(results):
            if isinstance(result, list):
                count = len(result)
                print(f"   📥 Из {source_names[i]} получено: {count} новостей")
                all_news.extend(result)
                if count > 0:
                    active_sources += 1
            elif isinstance(result, Exception):
                print(f"   ⚠️ Ошибка при сборе из {source_names[i]}: {type(result).__name__}")
        
        if active_sources == 0:
             print("   ⚠️ Нет активных источников новостей.")
        print(f"📥 Всего собрано новостей: {len(all_news)}")
        return all_news

    def extract_timing_info(self, text):
        """Извлечение временной информации из текста"""
        timing_patterns = {
            'immediate': ['немедленно', 'сейчас', 'urgent', 'immediately', '立即', '马上'],
            'short_term': ['ближайшие дни', 'next few days', 'within a week', 'неделю', '几天内', '一周内'],
            'medium_term': ['2-4 недели', '2-4 weeks', 'next month', 'месяц', '几周', '一个月'],
            'long_term': ['несколько месяцев', 'several months', 'long term', 'длительный', '几个月', '长期']
        }
        
        text_lower = text.lower()
        timing_info = {}
        
        for period, keywords in timing_patterns.items():
            found = any(keyword in text_lower for keyword in keywords)
            if found:
                timing_info[period] = True
        
        numbers = re.findall(r'(\d+)\s*(день|дня|дней|day|days|неделя|недели|weeks|week|天|周)', text_lower)
        if numbers:
            timing_info['specific_numbers'] = numbers
            
        return timing_info

    def predict_impact_timing(self, news_item):
        """Предсказание времени наступления влияния события"""
        text = (news_item['title'] or '') + ' ' + (news_item.get('content', '') or '')
        timing_info = self.extract_timing_info(text)
        
        base_timing = {
            'production_halt': 0,      # Немедленно
            'maintenance': 7,          # 1 неделя
            'raw_material_price': 3,   # 3 дня
            'logistics_issue': 5,      # 5 дней
            'market_analysis': 14     # 2 недели
        }
        
        text_lower = text.lower()
        impact_timing = 1  # Дни до влияния (по умолчанию)
        
        if any(word in text_lower for word in ['форс-мажор', 'force majeure', 'halt', 'stop', '紧急停车', '停产']):
            impact_timing = base_timing['production_halt']
        elif any(word in text_lower for word in ['ремонт', 'maintenance', '检修', '大修']):
            impact_timing = base_timing['maintenance']
        elif any(word in text_lower for word in ['цены', 'price', 'аналин', 'aniline', 'бензол', 'benzene', 'газ', 'gas', '原料', '价格']):
            impact_timing = base_timing['raw_material_price']
        elif any(word in text_lower for word in ['доставка', 'логистика', 'shipping', 'logistics', '物流', '运输']):
            impact_timing = base_timing['logistics_issue']
        else:
            impact_timing = base_timing['market_analysis']
        
        if 'specific_numbers' in timing_info:
            try:
                number, unit = timing_info['specific_numbers'][0]
                number = int(number)
                if any(u in unit.lower() for u in ['день', 'day', '天']):
                    impact_timing = number
                elif any(u in unit.lower() for u in ['недел', 'week', '周']):
                    impact_timing = number * 7
            except Exception as e:
                pass
        
        return impact_timing

    def filter_mdi_relevant_news(self, all_news):
        """Фильтрация новостей, релевантных MDI с временными метками"""
        if not all_news:
            print("🔍 Нет новостей для фильтрации.")
            return []
        print(f"🔍 Начинаем фильтрацию {len(all_news)} новостей по релевантности...")
        relevant_news = []
        
        for news in all_news:
            title_lower = (news['title'] or '').lower()
            content_lower = (news.get('content', '') or '').lower()
            full_text = title_lower + ' ' + content_lower
            
            relevance_score = 0.0 # Начинаем с 0.0
            matched_keywords = []
            
            # --- Уточненная логика начисления баллов ---
            # Проверка по основным ключевым словам
            for keyword in self.keywords['main_keywords']:
                if keyword.lower() in full_text:
                    if keyword.lower() in ['mdi', 'мди']:
                        relevance_score += 2
                        matched_keywords.append(keyword)
                    elif keyword.lower() in ['wanhua', '万华化学', 'covestro', '科思创']:
                        relevance_score += 5 # Высокий вес для производителей
                        matched_keywords.append(keyword)
                    elif keyword.lower() in ['44v20l', 'pm200']:
                        relevance_score += 4 # Высокий вес для марок
                        matched_keywords.append(keyword)
                    elif keyword.lower() in ['производство', 'production', 'ремонт', 'maintenance', 'форс-мажор', 'force majeure', 'остановка', 'shutdown', 'расширение', 'expansion', '检修', '大修', '紧急停车', '扩产', '开车', '投产']:
                        relevance_score += 3 # Средний высокий вес для событий
                        matched_keywords.append(keyword)
                    elif keyword.lower() in ['анилин', 'aniline', 'фосген', 'phosgene', 'бензол', 'benzene', 'газ', 'gas', '氯气', '苯', '光气', '天然气', 'natural gas', 'crude benzene', '硝基苯']:
                        relevance_score += 3 # Средний высокий вес для сырья
                        matched_keywords.append(keyword)
                    elif keyword.lower() in ['цены', 'price', 'поставки', 'supply', 'дефицит', 'shortage', '产能', '产量', '现货价', '合同价']:
                        relevance_score += 1.5 # Средний вес для рыночных условий
                        matched_keywords.append(keyword)
                    else:
                        relevance_score += 1
                        matched_keywords.append(keyword)
            
            # Проверка по региональным ключевым словам (фокус на Китае)
            # Усиливаем общий скор, если есть региональное упоминание
            regional_boost = 1.0
            for region, region_keywords in self.keywords['regional'].items():
                for keyword in region_keywords:
                    if keyword.lower() in full_text:
                        regional_boost *= 1.2 # Усиление на 20% за каждое упоминание
                        matched_keywords.append(f"{keyword}({region})")
            relevance_score *= regional_boost

            # Проверка по ключевым словам воздействия (более деликатное начисление)
            for impact_category in [self.keywords['high_impact'], self.keywords['medium_impact'], self.keywords['low_impact'], self.keywords['logistics']]:
                for keyword in impact_category:
                    if keyword.lower() in full_text:
                        if keyword.lower() in self.keywords['high_impact']:
                            relevance_score += 1.5
                        elif keyword.lower() in self.keywords['medium_impact']:
                            relevance_score += 1.0
                        elif keyword.lower() in self.keywords['low_impact']:
                            relevance_score += 0.5
                        elif keyword.lower() in self.keywords['logistics']:
                            relevance_score += 0.7
                        matched_keywords.append(keyword)
            
            # --- Увеличенный порог релевантности ---
            if relevance_score >= 2.5: # Повышенный порог для качества
                # ... (остальная логика: предсказание времени, добавление в список)
                impact_timing = self.predict_impact_timing(news)
                news_date = news['date']
                if news_date.tzinfo is None:
                    news_date = news_date.replace(tzinfo=timezone.utc)
                impact_date = news_date + timedelta(days=impact_timing)
                
                news['relevance_score'] = relevance_score
                news['matched_keywords'] = matched_keywords
                news['impact_timing_days'] = impact_timing
                news['predicted_impact_date'] = impact_date
                news['impact_timeline'] = self.get_timeline_category(impact_timing)
                
                relevant_news.append(news)
        
        relevant_news.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        print(f"🎯 Отфильтровано релевантных новостей: {len(relevant_news)}")
        return relevant_news

    def get_timeline_category(self, days):
        """Категоризация временных рамок"""
        if days <= 1:
            return 'immediate'
        elif days <= 7:
            return 'short_term'
        elif days <= 30:
            return 'medium_term'
        else:
            return 'long_term'

    def get_news_with_timeline(self, hours=120): # 5 дней по умолчанию
        """Получение новостей с прогнозом времени влияния"""
        print(f"🕒 Поиск новостей за последние {hours} часов...")
        async def fetch_and_filter():
            all_news = await self.fetch_all_news_sources()
            relevant_news = self.filter_mdi_relevant_news(all_news)
            
            time_threshold = datetime.now(timezone.utc) - timedelta(hours=hours)
            recent_news = [
                news for news in relevant_news 
                if isinstance(news['date'], datetime) and news['date'] > time_threshold
            ]
            
            print(f"📅 Отобрано свежих новостей (после {time_threshold.strftime('%Y-%m-%d %H:%M')} UTC): {len(recent_news)}")
            return recent_news
        
        return asyncio.run(fetch_and_filter())

class EnhancedMDIAnalyzer(MDIAnalyzer):
    def __init__(self):
        super().__init__()
        self.news_integrator = NewsIntegrator()
    
    def fetch_news(self, days_back=5): # 5 дней по умолчанию
        """Получение релевантных новостей"""
        try:
            recent_news = self.news_integrator.get_news_with_timeline(hours=days_back*24)
            if len(recent_news) == 0:
                print("   ⚠️ Релевантных новостей не найдено.")
            return recent_news
        except Exception as e:
            print(f"⚠️ Ошибка при получении новостей: {type(e).__name__}")
            return []

    def analyze_news_item(self, news_item):
        """Полный анализ одной новости"""
        title = news_item.get('title', '')
        content = news_item.get('content', '')
        full_text = (title or '') + ' ' + (content or '')
        
        sentiment = self.analyze_sentiment(full_text)
        impact_score = min(news_item.get('relevance_score', 0) / 10, 1.0)
        total_impact = impact_score * (1 + sentiment * 0.2)
        total_impact = max(0, min(total_impact, 1))
        
        return {
            'news_item': news_item,
            'sentiment': sentiment,
            'impact_score': impact_score,
            'total_impact': total_impact,
            'analysis_date': datetime.now(timezone.utc)
        }

    def run_full_analysis(self):
        """Полный цикл анализа (БЕЗ прогноза цен)"""
        print("🚀 Запуск ИИ-анализа рынка MDI...")
        print("=" * 60)
        print("ℹ️  Анализ фокусируется на китайском рынке и производителях (Wanhua, Covestro, Huntsman).")
        print("ℹ️  Закупка Covestro 44V20L осуществляется только в Китае.")
        
        news = self.fetch_news(days_back=5)
        print(f"📥 Получено {len(news)} релевантных новостей")
        
        if not news:
            print("❌ Нет релевантных новостей для анализа.")
            return {
                'analysis_results': [],
                'message': 'Нет релевантных новостей'
            }
        
        analysis_results = []
        for news_item in news:
            analysis = self.analyze_news_item(news_item)
            analysis_results.append(analysis)
        
        print(f"📊 Проанализировано {len(analysis_results)} новостей")
        
        # --- УДАЛЕН прогноз цен ---
        
        print("\n🔍 ДЕТАЛИЗАЦИЯ АНАЛИЗА (Топ-20 новостей):")
        for i, result in enumerate(analysis_results[:20], 1): # Показываем больше новостей
            news_item = result['news_item']
            print(f"{i}. {news_item['title']}")
            print(f"   Дата: {news_item['date'].strftime('%Y-%m-%d %H:%M')} UTC")
            print(f"   Источник: {news_item['source']} [{news_item['category']}]")
            print(f"   Влияние (Score): {result['total_impact']:.2f}")
            print(f"   Настроение: {result['sentiment']:+.2f}")
            print(f"   Релевантность: {news_item.get('relevance_score', 0):.2f}") # Более точный вывод
            if 'matched_keywords' in news_item:
                print(f"   Ключевые слова: {', '.join(news_item['matched_keywords'][:10])}") # Показываем больше ключевых слов
            if 'predicted_impact_date' in news_item:
                print(f"   Влияние ожидается: {news_item['predicted_impact_date'].strftime('%Y-%m-%d')} UTC ({news_item['impact_timing_days']} дней)")
            print()
        
        # --- Возвращаем только результаты анализа ---
        return {
            'analysis_results': analysis_results
        }

# Пример использования
if __name__ == "__main__":
    print("🚀 Запуск улучшенного MDI анализатора...")
    
    # --- ОТЛАДОЧНЫЙ ВЫВОД ДЛЯ ПРОВЕРКИ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
    print(f"Текущая директория: {os.getcwd()}")
    print(f"Значение os.environ.get('NEWSAPI_KEY'): {os.environ.get('NEWSAPI_KEY')}")
    # Проверим, существует ли файл .env и попробуем прочитать его напрямую
    env_path = '.env'
    if os.path.exists(env_path):
        print(f"Файл {env_path} существует.")
        try:
            with open(env_path, 'r') as f:
                content = f.read()
                print(f"Содержимое .env (первые 200 символов): {content[:200]}")
        except Exception as e:
            print(f"Ошибка чтения .env: {e}")
    else:
        print(f"Файл {env_path} НЕ существует в текущей директории.")
    # ------------------------------------------------------------
    
    enhanced_analyzer = EnhancedMDIAnalyzer()
    
    results = enhanced_analyzer.run_full_analysis()
    
    print("✅ Анализ завершен!")
    # Опционально: сохранить результаты в файл
    # with open('mdi_news_analysis_results.json', 'w', encoding='utf-8') as f:
    #     json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    # print("💾 Результаты сохранены в mdi_news_analysis_results.json")