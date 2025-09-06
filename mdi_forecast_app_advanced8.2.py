# mdi_forecast_app_advanced.py
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import os
import warnings
import json
from io import BytesIO
import base64
import nest_asyncio
import asyncio
# Для LSTM
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping
import plotly.graph_objects as go
from plotly.subplots import make_subplots
warnings.filterwarnings('ignore')
# Разрешаем вложенные event loops
nest_asyncio.apply()
# --- Импорт вашего модуля для анализа новостей ---
# Убедитесь, что файл news_rss_api_upd5.py находится в той же папке
try:
    from news_rss_api_upd5 import EnhancedMDIAnalyzer
except ImportError as e:
    st.error(f"Не удалось импортировать EnhancedMDIAnalyzer. Убедитесь, что файл 'news_rss_api_upd5.py' находится в одной папке с этим скриптом. Ошибка: {e}")
    st.stop()
# --- Функция для асинхронного запуска анализа новостей ---
async def run_news_analysis_async(days_back=5):
    """Запускает анализ новостей."""
    try:
        analyzer = EnhancedMDIAnalyzer()
        # Проверяем, является ли метод асинхронным
        if asyncio.iscoroutinefunction(analyzer.news_integrator.get_news_with_timeline):
            results = await analyzer.news_integrator.get_news_with_timeline(hours=days_back*24)
        else:
            # Если метод синхронный, вызываем его напрямую
            results = analyzer.news_integrator.get_news_with_timeline(hours=days_back*24)
        analysis_results = []
        for news_item in results:
            analysis = analyzer.analyze_news_item(news_item)
            analysis_results.append(analysis)
        return {'analysis_results': analysis_results}
    except Exception as e:
        st.error(f"Ошибка во время асинхронного анализа новостей: {e}")
        return {'analysis_results': [], 'error': str(e)}
def run_news_analysis(days_back=5):
    """Синхронная обертка для запуска асинхронного анализа."""
    try:
        # Проверяем, есть ли уже запущенный event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Если loop уже запущен, создаем task
                task = loop.create_task(run_news_analysis_async(days_back))
                # В Streamlit можем использовать run_until_complete только если loop не активен
                # Для упрощения возвращаем пустой результат
                return {'analysis_results': []}
            else:
                results = loop.run_until_complete(run_news_analysis_async(days_back))
                return results
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(run_news_analysis_async(days_back))
            return results
    except Exception as e:
        st.error(f"Ошибка при запуске анализа новостей: {e}")
        return {'analysis_results': [], 'error': str(e)}
# --- Функция для получения данных по акциям производителей ---
@st.cache_data(ttl=3600) # Кэшируем на 1 час
def get_producer_data():
    """Получает данные по акциям производителей MDI."""
    tickers = {
        "BAS.DE": "BASF",
        "1COV.DE": "Covestro", 
        "600309.SS": "Wanhua",
        "HUN": "Huntsman"  # Добавляем Huntsman
    }
    rows = []
    log_messages = []
    for tkr, name in tickers.items():
        try:
            df = yf.Ticker(tkr).history(period="max")
            if not df.empty:
                df = df[["Close"]].rename(columns={"Close": "Price"})
                df.index.name = "Date"
                df = df.reset_index()
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.date
                df["Producer"] = name
                rows.append(df)
        except Exception as e:
            error_msg = f"Не удалось загрузить данные для {name} ({tkr}): {e}"
            log_messages.append(error_msg)
    result = pd.concat(rows, ignore_index=True).sort_values(["Date", "Producer"]) if rows else pd.DataFrame(columns=["Date", "Price", "Producer"])
    # Сохраняем логи в session_state
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    st.session_state.log_messages.extend(log_messages)
    return result
# --- Функция для получения данных по сырью ---
@st.cache_data(ttl=3600) # Кэшируем на 1 час
def get_raw_material_data():
    """Получает данные по ценам на сырье."""
    tickers = {
        "Aniline": "BZ=F",   # Brent crude - прокси для бензола/анилина
        "NaturalGas": "NG=F"  # Henry Hub
    }
    rows = []
    log_messages = []
    for comp, ticker in tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="max")
            if not df.empty:
                df = df[["Close"]].rename(columns={"Close": "Price"})
                df.index.name = "Date"
                df = df.reset_index()
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.date
                df["Component"] = comp
                rows.append(df)
        except Exception as e:
            error_msg = f"Не удалось загрузить данные для {comp} ({ticker}): {e}"
            log_messages.append(error_msg)
    result = pd.concat(rows, ignore_index=True).sort_values(["Date", "Component"]) if rows else pd.DataFrame(columns=["Date", "Price", "Component"])
    # Сохраняем логи в session_state
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    st.session_state.log_messages.extend(log_messages)
    return result
# --- Функция для загрузки данных о ценах на MDI из Excel ---
# ВАЖНО: Убираем @st.cache_data, чтобы данные всегда перезагружались
# @st.cache_data
def load_mdi_price_data(file_path=None):
    """Загружает данные о ценах на MDI из Excel файла."""
    log_messages = []
    if file_path is None:
        log_messages.append("Файл с данными MDI не загружен.")
        # Не добавляем в логи, так как это не ошибка
        return pd.DataFrame()
    else:
        # Загрузка из файла
        try:
            df = pd.read_excel(file_path)
            log_messages.append(f"Excel файл успешно загружен. Найдено {len(df)} строк.")
            if 'Data Date' in df.columns:
                # Переименовываем колонку для согласованности
                df = df.rename(columns={'Data Date': 'Date'})
                log_messages.append("Столбец 'Data Date' успешно переименован в 'Date'.")
            if 'Date' in df.columns:
                initial_count = len(df)
                log_messages.append(f"Начальное количество строк с датами: {initial_count}")
                # Пробуем разные форматы дат
                original_dates = df['Date'].copy()
                # Формат dd.mm.yyyy (например, 30.06.2025)
                df['Date'] = pd.to_datetime(df['Date'], format='%d.%m.%Y', errors='coerce')
                # Проверяем, сколько дат успешно преобразовалось
                successfully_parsed = df['Date'].notna().sum()
                log_messages.append(f"Успешно преобразовано дат в формате dd.mm.yyyy: {successfully_parsed}")
                # Если не все даты преобразованы, пробуем другие форматы
                if successfully_parsed < initial_count:
                    log_messages.append("Не все даты были успешно преобразованы в формате dd.mm.yyyy. Пробуем другие форматы...")
                    unparsed_mask = df['Date'].isna()
                    unparsed_dates = original_dates[unparsed_mask]
                    log_messages.append(f"Необработанные даты: {unparsed_dates.tolist()[:10]}...") # Показываем первые 10
                    # Пробуем формат dd-mmm-yy (например, 23-Jun-25)
                    df.loc[unparsed_mask, 'Date'] = pd.to_datetime(unparsed_dates, format='%d-%b-%y', errors='coerce')
                    new_successfully_parsed = df['Date'].notna().sum()
                    log_messages.append(f"После попытки формата dd-mmm-yy успешно преобразовано: {new_successfully_parsed}")
                    # Если все еще есть необработанные, используем автоматическое определение
                    if new_successfully_parsed < initial_count:
                        remaining_unparsed_mask = df['Date'].isna()
                        remaining_unparsed_dates = original_dates[remaining_unparsed_mask]
                        log_messages.append(f"Осталось необработанных дат: {remaining_unparsed_dates.count()}. Пробуем автоматическое определение...")
                        df.loc[remaining_unparsed_mask, 'Date'] = pd.to_datetime(remaining_unparsed_dates, infer_datetime_format=True, errors='coerce')
                        final_successfully_parsed = df['Date'].notna().sum()
                        log_messages.append(f"После автоматического определения успешно преобразовано: {final_successfully_parsed}")
                # Преобразуем в date
                df['Date'] = df['Date'].dt.date
                # Проверяем, есть ли вообще даты
                if df['Date'].isna().all():
                    log_messages.append("Ошибка: Не удалось преобразовать ни одну дату из файла. Проверьте формат дат в Excel файле.")
                    return pd.DataFrame()
                else:
                    successfully_converted_to_date = df['Date'].notna().sum()
                    log_messages.append(f"Успешно преобразовано и конвертировано в объекты date: {successfully_converted_to_date}")
                # Убедимся, что числовые столбцы корректно обработаны
                numeric_columns = ['Lowest', 'Highest', 'Mainstream', 'Chg']
                for col in numeric_columns:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        log_messages.append(f"Столбец '{col}' преобразован в числовой формат. Не-числовые значения заменены на NaN.")
                # Обрабатываем Chg% если он есть
                if 'Chg%' in df.columns:
                    # Убираем символ %, заменяем запятую на точку и преобразуем в число
                    df['Chg%'] = df['Chg%'].astype(str).str.rstrip('%').str.replace(',', '.').astype('float') / 100.0
                    log_messages.append("Столбец 'Chg%' успешно обработан.")
                # Показываем диапазон дат
                if df['Date'].notna().any():
                    min_date = df['Date'].min()
                    max_date = df['Date'].max()
                    log_messages.append(f"Диапазон дат в файле: с {min_date} по {max_date}")
                else:
                    log_messages.append("В файле не найдено корректных дат.")
                # Сохраняем логи в session_state
                if 'log_messages' not in st.session_state:
                    st.session_state.log_messages = []
                st.session_state.log_messages.extend(log_messages)
                return df
            else:
                log_messages.append("Ошибка: В Excel файле отсутствует столбец 'Date' или 'Data Date'.")
                # Сохраняем логи в session_state
                if 'log_messages' not in st.session_state:
                    st.session_state.log_messages = []
                st.session_state.log_messages.extend(log_messages)
                return pd.DataFrame()
        except Exception as e:
            error_msg = f"Ошибка загрузки файла: {e}"
            log_messages.append(error_msg)
            # Сохраняем логи в session_state
            if 'log_messages' not in st.session_state:
                st.session_state.log_messages = []
            st.session_state.log_messages.extend(log_messages)
            import traceback
            st.session_state.log_messages.append(traceback.format_exc())
            return pd.DataFrame()
# --- Функция для подготовки данных для прогноза ---
def prepare_forecast_data(mdi_df, producer_df, raw_material_df, news_results):
    """Подготавливает данные для прогноза, объединяя все источники."""
    log_messages = []
    # Фильтруем данные с 01.09.2024
    start_date = datetime(2024, 9, 1).date()
    # Фильтруем MDI данные
    if not mdi_df.empty:
        # Убедимся, что столбец Date существует и имеет правильный тип
        if 'Date' in mdi_df.columns:
            mdi_df = mdi_df.copy()  # Создаем копию чтобы не модифицировать оригинальный DataFrame
            # mdi_df['Date'] = pd.to_datetime(mdi_df['Date'], errors='coerce').dt.date
            # Уже преобразовано в load_mdi_price_data
            # Фильтруем данные с начальной даты
            total_rows_before_filter = len(mdi_df)
            mdi_df = mdi_df[mdi_df['Date'] >= start_date]
            rows_after_filter = len(mdi_df)
            log_messages.append(f"Фильтрация MDI данных с {start_date}: было {total_rows_before_filter} строк, осталось {rows_after_filter} строк.")
            if mdi_df.empty:
                log_messages.append(f"После фильтрации с {start_date} не осталось данных MDI. Возможно, все даты в файле раньше этой даты.")
        else:
            log_messages.append("В данных MDI отсутствует столбец 'Date'")
            # Сохраняем логи в session_state
            if 'log_messages' not in st.session_state:
                st.session_state.log_messages = []
            st.session_state.log_messages.extend(log_messages)
            return pd.DataFrame()
    else:
        log_messages.append("Нет данных MDI")
        # Сохраняем логи в session_state
        if 'log_messages' not in st.session_state:
            st.session_state.log_messages = []
        st.session_state.log_messages.extend(log_messages)
        return pd.DataFrame()
    # Создаем базовый DataFrame с датами
    if mdi_df.empty:
        log_messages.append("Нет данных MDI для прогнозирования в заданном периоде")
        # Сохраняем логи в session_state
        if 'log_messages' not in st.session_state:
            st.session_state.log_messages = []
        st.session_state.log_messages.extend(log_messages)
        return pd.DataFrame()
    # Определяем диапазон дат из данных MDI
    actual_start_date = mdi_df['Date'].min()
    actual_end_date = mdi_df['Date'].max()
    all_dates = pd.date_range(start=actual_start_date, end=actual_end_date, freq='D')
    all_dates = [d.date() for d in all_dates]
    df = pd.DataFrame({'Date': all_dates})
    log_messages.append(f"Создан базовый DataFrame с датами от {actual_start_date} до {actual_end_date} ({len(all_dates)} дней).")
    # Добавляем цены на MDI
    if not mdi_df.empty and 'Mainstream' in mdi_df.columns:
        # Используем только столбец Mainstream для цены MDI
        mdi_prices = mdi_df[['Date', 'Mainstream']].rename(columns={'Mainstream': 'MDI_Price'})
        # Удаляем дубликаты по дате, оставляя последнее значение
        initial_mdiprices_len = len(mdi_prices)
        mdi_prices = mdi_prices.drop_duplicates(subset=['Date'], keep='last')
        final_mdiprices_len = len(mdi_prices)
        if initial_mdiprices_len != final_mdiprices_len:
            log_messages.append(f"Удалены дубликаты в данных MDI: было {initial_mdiprices_len}, осталось {final_mdiprices_len}.")
        df = df.merge(mdi_prices, on='Date', how='left')
        merged_count = df['MDI_Price'].notna().sum()
        log_messages.append(f"Цены MDI добавлены. Найдено {merged_count} совпадений по датам.")
    else:
        log_messages.append("В данных MDI отсутствует столбец 'Mainstream'")
        # Сохраняем логи в session_state
        if 'log_messages' not in st.session_state:
            st.session_state.log_messages = []
        st.session_state.log_messages.extend(log_messages)
        return pd.DataFrame()
    # Добавляем цены на акции производителей
    producers_added = 0
    if not producer_df.empty:
        # Фильтруем данные по датам из MDI
        producer_df_filtered = producer_df[(producer_df['Date'] >= actual_start_date) & (producer_df['Date'] <= actual_end_date)].copy()
        if not producer_df_filtered.empty:
            producer_df_filtered['Date'] = pd.to_datetime(producer_df_filtered['Date']).dt.date
            producer_pivot = producer_df_filtered.pivot(index='Date', columns='Producer', values='Price')
            producer_pivot = producer_pivot.fillna(method='ffill').fillna(method='bfill').fillna(0)
            df = df.merge(producer_pivot, left_on='Date', right_index=True, how='left')
            producers_added = producer_pivot.shape[1] # Количество добавленных столбцов
            log_messages.append(f"Добавлены данные по акциям {producers_added} производителей.")
        else:
             log_messages.append("Нет данных по акциям производителей за указанный период.")
    else:
        log_messages.append("Нет общих данных по акциям производителей.")
    # Добавляем цены на сырье
    materials_added = 0
    if not raw_material_df.empty:
        # Фильтруем данные по датам из MDI
        raw_material_df_filtered = raw_material_df[(raw_material_df['Date'] >= actual_start_date) & (raw_material_df['Date'] <= actual_end_date)].copy()
        if not raw_material_df_filtered.empty:
            raw_material_df_filtered['Date'] = pd.to_datetime(raw_material_df_filtered['Date']).dt.date
            raw_material_pivot = raw_material_df_filtered.pivot(index='Date', columns='Component', values='Price')
            raw_material_pivot = raw_material_pivot.fillna(method='ffill').fillna(method='bfill').fillna(0)
            df = df.merge(raw_material_pivot, left_on='Date', right_index=True, how='left')
            materials_added = raw_material_pivot.shape[1] # Количество добавленных столбцов
            log_messages.append(f"Добавлены данные по ценам на {materials_added} видов сырья.")
        else:
             log_messages.append("Нет данных по ценам на сырье за указанный период.")
    else:
        log_messages.append("Нет общих данных по ценам на сырье.")
    # Добавляем столбец для флага "новость"
    df['News_Event_Score'] = 0.0
    news_added = 0
    if news_results and 'analysis_results' in news_results:
        for result in news_results['analysis_results']:
            try:
                if 'news_item' in result and 'date' in result['news_item']:
                    news_date = result['news_item']['date'].date()
                    if actual_start_date <= news_date <= actual_end_date:
                        impact_score = result.get('total_impact', 0) * result.get('sentiment', 0)
                        for i in range(5):  # Влияние в течение 5 дней
                            target_date = news_date + timedelta(days=i)
                            if target_date <= actual_end_date:
                                df.loc[df['Date'] == target_date, 'News_Event_Score'] += impact_score * (0.8 ** i)
                                news_added += 1
            except Exception as e:
                log_messages.append(f"Ошибка обработки новости: {e}")
        if news_added > 0:
            log_messages.append(f"Добавлено {news_added} записей о новостях.")
        else:
             log_messages.append("Новости не добавлены (возможно, вне диапазона дат).")
    else:
        log_messages.append("Новости не будут добавлены (нет данных или пустой результат анализа).")
    # Заполняем пропущенные значения нулями для числовых столбцов
    numeric_columns = df.select_dtypes(include=[np.number]).columns
    initial_na_counts = df[numeric_columns].isna().sum()
    df[numeric_columns] = df[numeric_columns].fillna(method='ffill').fillna(method='bfill').fillna(0)
    final_na_counts = df[numeric_columns].isna().sum()
    filled_any = (initial_na_counts > final_na_counts).any()
    if filled_any:
        log_messages.append("Пропущенные значения в числовых столбцах заполнены.")
    else:
        log_messages.append("Пропущенных значений в числовых столбцах не было или они уже заполнены.")
    # Удаляем строки без цен на MDI
    initial_len = len(df)
    if 'MDI_Price' in df.columns:
        df = df.dropna(subset=['MDI_Price'])
        final_len = len(df)
        if initial_len != final_len:
            log_messages.append(f"Удалены строки без цены MDI: было {initial_len}, осталось {final_len}.")
        else:
            log_messages.append(f"Все {final_len} строк содержат цены MDI.")
    else:
        log_messages.append("Столбец 'MDI_Price' отсутствует в итоговом DataFrame.")
    # Сохраняем логи в session_state
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    st.session_state.log_messages.extend(log_messages)
    st.success(f"Подготовка данных завершена. Итоговый DataFrame содержит {len(df)} строк и {len(df.columns)} столбцов.")
    return df
# --- Функция для создания наборов данных для LSTM ---
def create_dataset(dataset, look_back=1):
    """Преобразует массив временных рядов в наборы данных для обучения LSTM."""
    dataX, dataY = [], []
    for i in range(len(dataset) - look_back - 1):
        a = dataset[i:(i + look_back), :]
        dataX.append(a)
        dataY.append(dataset[i + look_back, 0])  # Предсказываем только MDI_Price
    return np.array(dataX), np.array(dataY)
# --- Функция для прогноза с использованием LSTM ---
def forecast_mdi_price_lstm(df, look_back=10, epochs=50, batch_size=1, neurons=50, dropout=0.2, train_split=0.8):
    """Прогнозирует цену на MDI с использованием LSTM."""
    # Проверка наличия данных
    if df.empty:
        st.error("Нет данных для обучения модели")
        return None, None, None, None, None
    # Выбираем только колонки с числовыми данными
    features = ['MDI_Price']
    # Добавляем доступные столбцы производителей
    producer_cols = ['BASF', 'Covestro', 'Wanhua', 'Huntsman']
    for col in producer_cols:
        if col in df.columns:
            features.append(col)
    # Добавляем доступные столбцы сырья
    raw_material_cols = ['Aniline', 'NaturalGas']
    for col in raw_material_cols:
        if col in df.columns:
            features.append(col)
    # Добавляем столбец новостей если он есть
    if 'News_Event_Score' in df.columns:
        features.append('News_Event_Score')
    if len(features) == 0:
        st.error("Нет доступных признаков для обучения модели")
        return None, None, None, None, None
    if len(df) < look_back + 10:  # Минимальное количество данных
        st.error(f"Недостаточно данных для обучения. Требуется минимум {look_back + 10} записей, доступно {len(df)}")
        return None, None, None, None, None
    data = df[features].copy()
    # Заполняем пропущенные значения
    data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)
    # Нормализация данных
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled_data = scaler.fit_transform(data)
    # Разделение на обучающую и тестовую выборки
    train_size = max(int(len(scaled_data) * train_split), look_back + 5)  # Минимум look_back + 5 для обучения
    if train_size >= len(scaled_data) - look_back - 5:
        train_size = len(scaled_data) - look_back - 5
    if train_size <= look_back + 1:
        st.error("Недостаточно данных для создания обучающих наборов")
        return None, None, None, None, None
    train, test = scaled_data[0:train_size,:], scaled_data[train_size:len(scaled_data),:]
    # Создание наборов данных для обучения и тестирования
    trainX, trainY = create_dataset(train, look_back)
    testX, testY = create_dataset(test, look_back)
    # Проверка, что наборы данных не пустые
    if len(trainX) == 0:
        st.error("Недостаточно данных для создания обучающего набора")
        return None, None, None, None, None
    if len(testX) == 0:
        st.warning("Недостаточно данных для создания тестового набора. Используем только обучающий набор.")
        # Создаем минимальный тестовый набор
        if len(trainX) > 1:
            testX, testY = trainX[-1:], trainY[-1:]
            trainX, trainY = trainX[:-1], trainY[:-1]
        else:
            st.error("Недостаточно данных для разделения на обучающий и тестовый наборы")
            return None, None, None, None, None
    # Изменение формы данных для LSTM [samples, time steps, features]
    try:
        trainX = np.reshape(trainX, (trainX.shape[0], trainX.shape[1], len(features)))
        testX = np.reshape(testX, (testX.shape[0], testX.shape[1], len(features)))
    except Exception as e:
        st.error(f"Ошибка при изменении формы данных: {e}")
        return None, None, None, None, None
    # Создание и обучение модели LSTM
    model = Sequential()
    model.add(LSTM(neurons, return_sequences=True, input_shape=(look_back, len(features))))
    model.add(Dropout(dropout))
    model.add(LSTM(neurons, return_sequences=False))
    model.add(Dropout(dropout))
    model.add(Dense(25))
    model.add(Dense(1))
    model.compile(loss='mean_squared_error', optimizer='adam')
    # Обучение модели с ранней остановкой
    early_stop = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)
    try:
        model.fit(trainX, trainY, validation_data=(testX, testY), epochs=epochs, batch_size=batch_size, verbose=0, callbacks=[early_stop])
    except Exception as e:
        st.error(f"Ошибка при обучении модели: {e}")
        return None, None, None, None, None
    # Прогнозирование
    try:
        trainPredict = model.predict(trainX)
        testPredict = model.predict(testX)
    except Exception as e:
        st.error(f"Ошибка при прогнозировании: {e}")
        return None, None, None, None, None
    # Обратное преобразование предсказаний
    trainPredict_extended = np.zeros((len(trainPredict), len(features)))
    trainPredict_extended[:,0] = trainPredict[:,0]
    trainPredict = scaler.inverse_transform(trainPredict_extended)[:,0]
    testPredict_extended = np.zeros((len(testPredict), len(features)))
    testPredict_extended[:,0] = testPredict[:,0]
    testPredict = scaler.inverse_transform(testPredict_extended)[:,0]
    trainY_extended = np.zeros((len(trainY), len(features)))
    trainY_extended[:,0] = trainY
    trainY = scaler.inverse_transform(trainY_extended)[:,0]
    testY_extended = np.zeros((len(testY), len(features)))
    testY_extended[:,0] = testY
    testY = scaler.inverse_transform(testY_extended)[:,0]
    # Вычисление метрик
    trainScore = np.sqrt(mean_squared_error(trainY, trainPredict)) if len(trainY) > 0 else 0
    testScore = np.sqrt(mean_squared_error(testY, testPredict)) if len(testY) > 0 else 0
    # Создание DataFrame для результатов
    results_df = df.copy()
    results_df['Predicted_Price'] = np.nan
    results_df['Train/Test'] = ''
    # Заполнение предсказаний
    if len(trainPredict) > 0:
        train_dates = df.iloc[look_back:len(trainPredict)+look_back]['Date']
        results_df.loc[results_df['Date'].isin(train_dates), 'Predicted_Price'] = trainPredict
        results_df.loc[results_df['Date'].isin(train_dates), 'Train/Test'] = 'Train'
    if len(testPredict) > 0:
        test_start_idx = len(trainPredict) + (look_back*2) + 1
        test_end_idx = test_start_idx + len(testPredict)
        if test_end_idx <= len(df):
            test_dates = df.iloc[test_start_idx:test_end_idx]['Date']
            results_df.loc[results_df['Date'].isin(test_dates), 'Predicted_Price'] = testPredict
            results_df.loc[results_df['Date'].isin(test_dates), 'Train/Test'] = 'Test'
    return results_df, trainScore, testScore, model, scaler
# --- Функция для прогноза на будущее с использованием обученной модели ---
def forecast_future(model, scaler, last_sequence, n_future_days, features):
    """Прогнозирует цены на будущие дни."""
    future_predictions = []
    current_batch = last_sequence.copy()
    for _ in range(n_future_days):
        # Изменение формы данных для прогноза
        current_batch_reshaped = current_batch.reshape((1, current_batch.shape[0], current_batch.shape[1]))
        # Прогнозирование следующего значения
        next_pred = model.predict(current_batch_reshaped, verbose=0)
        # Добавление предсказания в список
        future_predictions.append(next_pred[0, 0])
        # Обновление последовательности
        new_row = current_batch[-1].copy()  # Берем последнюю строку
        new_row[0] = next_pred[0, 0]  # Обновляем только MDI_Price
        current_batch = np.append(current_batch[1:], [new_row], axis=0)  # Сдвигаем и добавляем новую строку
    # Обратное преобразование предсказаний
    future_predictions_array = np.array(future_predictions)
    # Создаем временный массив для обратного преобразования
    temp_array = np.zeros((len(future_predictions_array), len(features)))
    temp_array[:, 0] = future_predictions_array
    future_predictions_inv = scaler.inverse_transform(temp_array)[:, 0]
    return future_predictions_inv
# --- Главная функция Streamlit ---
def main():
    st.set_page_config(page_title="Прогноз стоимости MDI (Расширенный)", layout="wide")
    st.title("📊 Прогноз стоимости MDI (Расширенный)")
    # Инициализация session_state для логов
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    # --- Боковая панель ---
    st.sidebar.header("Настройки")
    # Настройки сбора данных
    st.sidebar.subheader("Сбор данных")
    days_back = st.sidebar.slider("Период поиска новостей (дни)", min_value=1, max_value=14, value=5, step=1)
    run_news_button = st.sidebar.button("Обновить новости")
    upload_file = st.sidebar.file_uploader("Загрузите Excel-файл с ценами на MDI", type=["xlsx", "xls"])
    # Настройки модели
    st.sidebar.subheader("Настройки модели LSTM")
    look_back = st.sidebar.slider("Количество дней для анализа (look_back)", min_value=5, max_value=30, value=10, step=1)
    epochs = st.sidebar.slider("Количество эпох обучения", min_value=10, max_value=200, value=50, step=10)
    batch_size = st.sidebar.selectbox("Размер батча", options=[1, 16, 32, 64], index=0)
    neurons = st.sidebar.slider("Количество нейронов в слое", min_value=20, max_value=200, value=50, step=10)
    dropout = st.sidebar.slider("Dropout (регуляризация)", min_value=0.0, max_value=0.5, value=0.2, step=0.05)
    train_split = st.sidebar.slider("Доля обучающей выборки", min_value=0.5, max_value=0.95, value=0.8, step=0.05)
    forecast_days = st.sidebar.slider("Количество дней для прогноза", min_value=10, max_value=180, value=60, step=10)
    # Кнопка запуска прогноза
    run_forecast_button = st.sidebar.button("Запустить прогноз")
    # --- Получение данных ---
    # ВАЖНО: load_all_data не кэшируется, чтобы данные всегда перезагружались
    # @st.cache_data(show_spinner=False)
    def load_all_data():
        with st.spinner('Загрузка финансовых данных...'):
            producer_data = get_producer_data()
            raw_material_data = get_raw_material_data()
        # ВАЖНО: load_mdi_price_data НЕ кэшируется
        mdi_data = load_mdi_price_data(upload_file)
        return producer_data, raw_material_data, mdi_data
    producer_data, raw_material_data, mdi_data = load_all_data()
    # --- Сохранение/Загрузка состояния ---
    st.sidebar.subheader("Сохранение/Загрузка")
    # Сохранение текущего состояния
    state_to_save = {
        "producer_data": producer_data.to_dict('records') if not producer_data.empty else [],
        "raw_material_data": raw_material_data.to_dict('records') if not raw_material_data.empty else [],
        "mdi_data": mdi_data.to_dict('records') if not mdi_data.empty else [],
        "last_run_params": {
            "days_back": days_back,
            "look_back": look_back,
            "epochs": epochs,
            "batch_size": batch_size,
            "neurons": neurons,
            "dropout": dropout,
            "train_split": train_split,
            "forecast_days": forecast_days
        },
        "log_messages": st.session_state.log_messages
    }
    state_json = json.dumps(state_to_save, default=str)
    b64 = base64.b64encode(state_json.encode()).decode()
    href = f'<a href="file/json;base64,{b64}" download="mdi_forecast_state.json">Сохранить состояние</a>'
    st.sidebar.markdown(href, unsafe_allow_html=True)
    # Загрузка состояния
    uploaded_state = st.sidebar.file_uploader("Загрузить состояние", type=["json"])
    if uploaded_state is not None:
        try:
            state_data = json.load(uploaded_state)
            # Восстановление данных из состояния
            producer_data = pd.DataFrame(state_data.get("producer_data", []))
            raw_material_data = pd.DataFrame(state_data.get("raw_material_data", []))
            mdi_data = pd.DataFrame(state_data.get("mdi_data", []))
            st.session_state.log_messages = state_data.get("log_messages", [])
            st.sidebar.success("Состояние загружено!")
            st.experimental_rerun()  # Перезапуск для применения новых данных
        except Exception as e:
            st.sidebar.error(f"Ошибка загрузки состояния: {e}")
    # --- Анализ новостей ---
    if 'news_results' not in st.session_state:
        st.session_state.news_results = None
    if run_news_button:
        with st.spinner('Идет сбор и анализ новостей...'):
            st.session_state.news_results = run_news_analysis(days_back=days_back)
            if st.session_state.news_results and 'analysis_results' in st.session_state.news_results and len(st.session_state.news_results['analysis_results']) > 0:
                st.sidebar.success(f"Найдено {len(st.session_state.news_results['analysis_results'])} релевантных новостей.")
            else:
                st.sidebar.warning("Нет релевантных новостей для анализа.")
    # --- Подготовка данных для прогноза ---
    combined_data = pd.DataFrame()
    if not mdi_data.empty:
        with st.spinner('Подготовка данных для прогноза...'):
            combined_data = prepare_forecast_data(mdi_data, producer_data, raw_material_data, st.session_state.news_results)
            if not combined_data.empty:
                st.success(f"Данные для прогноза успешно подготовлены. Всего строк: {len(combined_data)}")
            else:
                st.error("Не удалось подготовить данные для прогноза. Проверьте входные данные.")
    # --- Вкладки ---
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Данные", "Анализ новостей", "Исторические цены", "Прогноз", "Модель", "Logs"])
    with tab1:
        st.subheader("Анализ данных MDI и внешних факторов")
        # Фильтруем данные с 01.09.2024
        start_date = datetime(2024, 9, 1).date()
        # Фильтруем MDI данные
        if not mdi_data.empty:
            # Убедимся, что Date столбец имеет правильный тип
            mdi_data_copy = mdi_data.copy()
            if 'Date' in mdi_data_copy.columns:
                # mdi_data_copy['Date'] = pd.to_datetime(mdi_data_copy['Date'], errors='coerce').dt.date
                # Уже преобразовано в load_mdi_price_data
                mdi_filtered = mdi_data_copy[mdi_data_copy['Date'] >= start_date]
            else:
                mdi_filtered = pd.DataFrame()
        else:
            mdi_filtered = pd.DataFrame()
        # Фильтруем данные акций производителей
        if not producer_data.empty:
            producer_data_copy = producer_data.copy()
            if 'Date' in producer_data_copy.columns:
                producer_data_copy['Date'] = pd.to_datetime(producer_data_copy['Date'], errors='coerce').dt.date
                producer_filtered = producer_data_copy[producer_data_copy['Date'] >= start_date]
            else:
                producer_filtered = pd.DataFrame()
        else:
            producer_filtered = pd.DataFrame()
        # Фильтруем данные сырья
        if not raw_material_data.empty:
            raw_material_data_copy = raw_material_data.copy()
            if 'Date' in raw_material_data_copy.columns:
                raw_material_data_copy['Date'] = pd.to_datetime(raw_material_data_copy['Date'], errors='coerce').dt.date
                raw_material_filtered = raw_material_data_copy[raw_material_data_copy['Date'] >= start_date]
            else:
                raw_material_filtered = pd.DataFrame()
        else:
            raw_material_filtered = pd.DataFrame()
        if not mdi_filtered.empty:
            # Создаем два графика один под другим
            # Первый график: Цена MDI и акции производителей
            st.subheader("Цена MDI и акции производителей")
            fig_mdi_producers = go.Figure()
            # Добавляем линию MDI Price
            fig_mdi_producers.add_trace(
                go.Scatter(
                    x=mdi_filtered['Date'], 
                    y=mdi_filtered['Mainstream'], 
                    mode='lines+markers', 
                    name='MDI Price (Mainstream)',
                    line=dict(color='red', width=2),
                    yaxis='y1'
                )
            )
            # Добавляем цены на акции производителей
            available_producers = ['BASF', 'Covestro', 'Wanhua', 'Huntsman']
            colors = ['blue', 'green', 'orange', 'purple']
            if not producer_filtered.empty:
                for i, producer in enumerate(available_producers):
                    if producer in producer_filtered['Producer'].values:
                        producer_stock_data = producer_filtered[producer_filtered['Producer'] == producer]
                        fig_mdi_producers.add_trace(
                            go.Scatter(
                                x=producer_stock_data['Date'], 
                                y=producer_stock_data['Price'], 
                                mode='lines', 
                                name=f'{producer} (Акции)',
                                line=dict(color=colors[i % len(colors)], width=1, dash='dot'),
                                yaxis='y2'
                            )
                        )
            # Настройка осей (исправляем синтаксис)
            fig_mdi_producers.update_layout(
                title="Цена MDI и акции производителей",
                xaxis_title="Дата",
                yaxis=dict(
                    title="Цена MDI (Yuan/mt)",
                ),
                yaxis2=dict(
                    title="Цена акций (USD/EUR/CNY)",
                    anchor="x",
                    overlaying="y",
                    side="right"
                ),
                hovermode='x unified',
                height=500
            )
            # Задаем цвета для заголовков осей (исправляем синтаксис)
            fig_mdi_producers.update_yaxes(title_font_color="red", tickfont_color="red", selector=dict(name="y"))
            fig_mdi_producers.update_yaxes(title_font_color="blue", tickfont_color="blue", selector=dict(name="y2"))
            st.plotly_chart(fig_mdi_producers, use_container_width=True)
            # Второй график: Цена MDI и цены на сырье
            st.subheader("Цена MDI и цены на сырье")
            fig_mdi_raw = go.Figure()
            # Добавляем линию MDI Price (повторно для сравнения)
            fig_mdi_raw.add_trace(
                go.Scatter(
                    x=mdi_filtered['Date'], 
                    y=mdi_filtered['Mainstream'], 
                    mode='lines+markers', 
                    name='MDI Price (Mainstream) - Сырье',
                    line=dict(color='red', width=2),
                    yaxis='y1'
                )
            )
            # Добавляем цены на сырье
            available_materials = ['Aniline', 'NaturalGas']
            material_colors = ['brown', 'pink']
            if not raw_material_filtered.empty:
                for i, material in enumerate(available_materials):
                    if material in raw_material_filtered['Component'].values:
                        material_data = raw_material_filtered[raw_material_filtered['Component'] == material]
                        fig_mdi_raw.add_trace(
                            go.Scatter(
                                x=material_data['Date'], 
                                y=material_data['Price'], 
                                mode='lines', 
                                name=f'{material} (Сырье)',
                                line=dict(color=material_colors[i % len(material_colors)], width=1, dash='dot'),
                                yaxis='y2'
                            )
                        )
            # Настройка осей (исправляем синтаксис)
            fig_mdi_raw.update_layout(
                title="Цена MDI и цены на сырье",
                xaxis_title="Дата",
                yaxis=dict(
                    title="Цена MDI (Yuan/mt)",
                ),
                yaxis2=dict(
                    title="Цена сырья (USD)",
                    anchor="x",
                    overlaying="y",
                    side="right"
                ),
                hovermode='x unified',
                height=500
            )
            # Задаем цвета для заголовков осей (исправляем синтаксис)
            fig_mdi_raw.update_yaxes(title_font_color="red", tickfont_color="red", selector=dict(name="y"))
            fig_mdi_raw.update_yaxes(title_font_color="brown", tickfont_color="brown", selector=dict(name="y2"))
            st.plotly_chart(fig_mdi_raw, use_container_width=True)
            # Отображаем последние данные в виде таблицы
            st.subheader("Последние данные")
            # Таблица с последними ценами MDI
            st.write("**Последние цены MDI**")
            # Отбираем только нужные столбцы
            mdi_display_cols = ['Date', 'Mainstream', 'Lowest', 'Highest', 'Chg']
            if 'Chg%' in mdi_filtered.columns:
                mdi_display_cols.append('Chg%')
            # Проверяем, что столбцы существуют
            existing_mdi_cols = [col for col in mdi_display_cols if col in mdi_filtered.columns]
            if existing_mdi_cols:
                st.dataframe(mdi_filtered[existing_mdi_cols].head(10), use_container_width=True)
            else:
                st.write("Не найдены необходимые столбцы для отображения.")
        else:
            if upload_file is None:
                st.info("Пожалуйста, загрузите Excel файл с историческими данными по ценам MDI.")
            else:
                st.warning("Не удалось загрузить или обработать данные из Excel файла. Проверьте формат файла и даты.")
        # Кнопка для скачивания всех данных MDI
        if not mdi_filtered.empty:
            csv = mdi_filtered.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="Скачать данные MDI (CSV)",
                data=csv,
                file_name='mdi_historical_data.csv',
                mime='text/csv',
            )
    with tab2:
        st.subheader("Анализ новостей")
        if st.session_state.news_results and 'analysis_results' in st.session_state.news_results and len(st.session_state.news_results['analysis_results']) > 0:
            # Преобразуем результаты в DataFrame
            news_df = []
            for result in st.session_state.news_results['analysis_results']:
                news_item = result['news_item']
                news_row = {
                    'Title': news_item.get('title', ''),
                    'Published_Date_UTC': news_item.get('date', '').strftime('%Y-%m-%d %H:%M:%S') if hasattr(news_item.get('date'), 'strftime') else '',
                    'Source': news_item.get('source', ''),
                    'Total_Impact_Score': round(result.get('total_impact', 0), 4),
                    'Sentiment': round(result.get('sentiment', 0), 4),
                    'Relevance_Score': round(news_item.get('relevance_score', 0), 4),
                    'Impact_Timing_Days': news_item.get('impact_timing_days', 0),
                    'URL': news_item.get('url', '')
                }
                news_df.append(news_row)
            df_news = pd.DataFrame(news_df)
            st.dataframe(df_news, use_container_width=True)
            # Простая визуализация новостей
            if not df_news.empty:
                st.subheader("Анализ новостей - Визуализация")
                fig_news = make_subplots(specs=[[{"secondary_y": True}]])
                fig_news.add_trace(
                    go.Scatter(x=pd.to_datetime(df_news['Published_Date_UTC']), y=df_news['Total_Impact_Score'], mode='markers', name='Влияние', marker=dict(size=10, color=df_news['Sentiment'], colorscale='RdYlGn', showscale=True)),
                    secondary_y=False,
                )
                fig_news.update_layout(title="Новости: Влияние vs Дата (Цвет - Настроение)", xaxis_title="Дата", yaxis_title="Влияние")
                st.plotly_chart(fig_news, use_container_width=True)
        else:
            st.info("Нажмите кнопку 'Обновить новости' в боковой панели для запуска анализа.")
    with tab3:
        st.subheader("Исторические цены на MDI")
        # Используем только данные из Excel файла
        start_date = datetime(2024, 9, 1).date()
        if not mdi_data.empty:
            # Фильтруем данные с 01.09.2024
            mdi_data_copy = mdi_data.copy()
            if 'Date' in mdi_data_copy.columns:
                # mdi_data_copy['Date'] = pd.to_datetime(mdi_data_copy['Date'], errors='coerce').dt.date
                # Уже преобразовано
                mdi_filtered = mdi_data_copy[mdi_data_copy['Date'] >= start_date]
                if not mdi_filtered.empty:
                    fig_mdi_history = go.Figure()
                    fig_mdi_history.add_trace(go.Scatter(x=mdi_filtered['Date'], y=mdi_filtered['Mainstream'], mode='lines+markers', name='Mainstream Price', line=dict(color='red')))
                    fig_mdi_history.update_layout(title="Исторические цены на PolyMDI", xaxis_title="Дата", yaxis_title="Цена (Yuan/mt)", hovermode='x')
                    st.plotly_chart(fig_mdi_history, use_container_width=True)
                    # Отображаем таблицу данных
                    st.write("**Таблица исторических цен**")
                    display_columns = ['Date', 'Mainstream', 'Lowest', 'Highest', 'Chg']
                    if 'Chg%' in mdi_filtered.columns:
                        display_columns.append('Chg%')
                    available_columns = [col for col in display_columns if col in mdi_filtered.columns]
                    st.dataframe(mdi_filtered[available_columns])
                else:
                    st.warning("Нет данных для отображения исторических цен с 01.09.2024")
            else:
                st.warning("Некорректный формат даты в данных MDI")
        else:
            st.warning("Пожалуйста, загрузите Excel файл с историческими данными по ценам MDI")
    with tab4:
        st.subheader("Прогноз стоимости MDI")
        if not combined_data.empty and run_forecast_button:
            with st.spinner('Обучение модели и прогнозирование...'):
                try:
                    results_df, train_rmse, test_rmse, model, scaler = forecast_mdi_price_lstm(
                        combined_data, look_back, epochs, batch_size, neurons, dropout, train_split
                    )
                    if results_df is not None and not results_df.empty:
                        # Определяем доступные признаки
                        features = ['MDI_Price']
                        producer_cols = ['BASF', 'Covestro', 'Wanhua', 'Huntsman']
                        for col in producer_cols:
                            if col in combined_data.columns:
                                features.append(col)
                        raw_material_cols = ['Aniline', 'NaturalGas']
                        for col in raw_material_cols:
                            if col in combined_data.columns:
                                features.append(col)
                        if 'News_Event_Score' in combined_data.columns:
                            features.append('News_Event_Score')
                        # Прогноз на будущее
                        if len(features) > 0 and len(combined_data) >= look_back:
                            try:
                                last_sequence = scaler.transform(combined_data[features].tail(look_back).fillna(method='ffill').fillna(method='bfill').fillna(0).values)
                                future_dates = [combined_data['Date'].max() + timedelta(days=i) for i in range(1, forecast_days+1)]
                                future_predictions = forecast_future(model, scaler, last_sequence, forecast_days, features)
                                # Создание DataFrame для будущих прогнозов
                                future_df = pd.DataFrame({'Date': future_dates, 'Predicted_Price': future_predictions, 'Train/Test': 'Future'})
                                # Объединение результатов
                                final_results_df = pd.concat([results_df, future_df], ignore_index=True)
                                # Создаем график
                                fig_forecast = go.Figure()
                                # Исторические данные
                                fig_forecast.add_trace(go.Scatter(x=combined_data['Date'], y=combined_data['MDI_Price'], mode='lines', name='Историческая цена', line=dict(color='blue')))
                                # Прогноз на тестовой выборке
                                test_data = final_results_df[final_results_df['Train/Test'] == 'Test']
                                if not test_data.empty:
                                    fig_forecast.add_trace(go.Scatter(x=test_data['Date'], y=test_data['Predicted_Price'], mode='lines', name='Прогноз (Тест)', line=dict(color='green', dash='dot')))
                                # Будущий прогноз
                                future_data = final_results_df[final_results_df['Train/Test'] == 'Future']
                                if not future_data.empty:
                                    fig_forecast.add_trace(go.Scatter(x=future_data['Date'], y=future_data['Predicted_Price'], mode='lines', name='Будущий прогноз', line=dict(color='orange', dash='dash')))
                                fig_forecast.update_layout(
                                    title="Прогноз стоимости MDI (LSTM)",
                                    xaxis_title="Дата",
                                    yaxis_title="Цена (Yuan/mt)",
                                    hovermode='x unified'
                                )
                                st.plotly_chart(fig_forecast, use_container_width=True)
                                # Метрики
                                col1, col2 = st.columns(2)
                                if train_rmse is not None:
                                    col1.metric("RMSE (Обучение)", f"{train_rmse:.2f}")
                                if test_rmse is not None:
                                    col2.metric("RMSE (Тест)", f"{test_rmse:.2f}")
                                # Таблица прогноза
                                st.write("**Таблица прогноза**")
                                st.dataframe(final_results_df[['Date', 'MDI_Price', 'Predicted_Price', 'Train/Test']].tail(20))
                                # Кнопка для скачивания прогноза
                                forecast_csv = final_results_df.to_csv(index=False).encode('utf-8-sig')
                                st.download_button(
                                    label="Скачать прогноз (CSV)",
                                    data=forecast_csv,
                                    file_name='mdi_forecast_lstm.csv',
                                    mime='text/csv',
                                )
                            except Exception as e:
                                st.error(f"Ошибка при прогнозировании на будущее: {e}")
                        else:
                            st.warning("Недостаточно данных для прогнозирования")
                    else:
                        st.error("Ошибка в процессе прогнозирования")
                except Exception as e:
                    st.error(f"Ошибка во время прогнозирования: {e}")
                    st.exception(e)
        elif not run_forecast_button:
            st.info("Настройте параметры модели в боковой панели и нажмите 'Запустить прогноз'.")
        else:
            st.warning("Нет данных для прогнозирования. Убедитесь, что загружен Excel файл с данными.")
    with tab5:
        st.subheader("Информация о модели LSTM")
        st.markdown("""
        **Что такое LSTM?**
        LSTM (Long Short-Term Memory) - это тип рекуррентной нейронной сети (RNN), разработанный для изучения долгосрочных зависимостей во временных рядах. Она особенно эффективна для прогнозирования, так как может "запоминать" важные события из прошлого и учитывать их при принятии решений о будущем.
        **Как работает модель в этом приложении:**
        1. **Входные данные:** Исторические цены на MDI (из Excel файла), акции производителей (BASF, Covestro, Wanhua, Huntsman), цены на сырье (анилин, газ) и скор событий из новостей.
        2. **Подготовка данных:** Все данные нормализуются и преобразуются в последовательности фиксированной длины (`look_back`).
        3. **Архитектура модели:**
           - Два слоя LSTM с `Dropout` для регуляризации.
           - Полносвязные слои для финального прогноза.
        4. **Обучение:** Модель обучается минимизировать среднеквадратичную ошибку (MSE) между предсказанными и фактическими ценами.
        5. **Прогноз:** После обучения модель используется для прогнозирования будущих цен на основе последней доступной последовательности данных.
        **Настройки модели:**
        - **look_back:** Количество предыдущих дней, используемых для прогноза следующего дня.
        - **Эпохи:** Количество проходов по всему обучающему набору данных.
        - **Размер батча:** Количество примеров, обрабатываемых моделью за один шаг.
        - **Нейроны:** Количество единиц в каждом слое LSTM.
        - **Dropout:** Доля нейронов, случайным образом "отключающихся" во время обучения для предотвращения переобучения.
        - **Доля обучения:** Процент данных, используемых для обучения (остальное - для тестирования).
        """)
    with tab6:
        st.subheader("Логи системы")
        st.info("Все логи системы собраны в одном месте")
        if st.session_state.log_messages:
            # Создаем раскрывающийся список для каждого лога
            for i, log_message in enumerate(st.session_state.log_messages):
                with st.expander(f"Лог #{i+1}: {log_message[:50]}..."):
                    st.text(log_message)
        else:
            st.info("Нет доступных логов")
        # Кнопка для очистки логов
        if st.button("Очистить логи"):
            st.session_state.log_messages = []
            st.experimental_rerun()
# --- Запуск приложения ---
if __name__ == "__main__":
    # Убедимся, что HARDCODED_NEWSAPI_KEY доступен
    try:
        from news_rss_api_upd5 import HARDCODED_NEWSAPI_KEY
    except ImportError:
        HARDCODED_NEWSAPI_KEY = 'f7e7ee9e68c64bebab1f6447c9ecd67b'      
    main()