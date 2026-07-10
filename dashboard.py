import streamlit as st
import psycopg2
import pandas as pd

# Настройки интерфейса
st.set_page_config(page_title="Журнал операций лабораторий", page_icon="🔬", layout="wide")
st.title("🔬 Журнал операций и отгрузок за последний месяц")

# Подключение к БД
@st.cache_resource
# Теперь код выглядит так и сам читает данные из secrets.toml
def init_connection():
    return psycopg2.connect(**st.secrets["postgres"])


try:
    conn = init_connection()
except Exception as e:
    st.error(f"Ошибка подключения к базе данных: {e}")
    st.stop()

# 1. ЗАГРУЗКА ПОЛНОГО СПРАВОЧНИКА КЛИНИК И ИХ ЛАБОРАТОРИЙ
@st.cache_data(ttl=600)
def load_structure_dict():
    query = """
        SELECT 
            dc.id as clinic_id,
            dc.clinic_code,
            dc.clinic_name,
            dlc.lab_code
        FROM public.dict_clinics dc
        LEFT JOIN public.dict_labs_configs dlc ON dc.id = dlc.clinic_id AND dlc.is_active = true
        WHERE dc.is_active = true
        ORDER BY dc.clinic_name, dlc.lab_code;
    """
    return pd.read_sql_query(query, conn)

# 2. ЗАГРУЗКА ЖУРНАЛА ОПЕРАЦИЙ (За последние 30 дней)
@st.cache_data(ttl=600)
def load_audit_data():
    query = """
        SELECT 
            ate.id,
            ate.occurred_at as "Время",
            dc.clinic_name as "Клиника",
            ate.clinic_code,
            ate.lab_code as "Код лаб.",
            ate.session_id,
            ate.event_type as "Тип события",
            ate.device_serial as "S/N устройства",
            ate.vacuum_barcode_raw as "Баркод пробирки",
            ate.cryo_barcode as "Крио-баркод",
            ate.sync_result as "Статус синхр.",
            ate.error_details as "Детали ошибки"
        FROM public.audit_trail_events ate
        LEFT JOIN public.dict_clinics dc ON ate.clinic_code = dc.clinic_code
        WHERE ate.occurred_at >= NOW() - INTERVAL '30 days'
        ORDER BY ate.occurred_at DESC;
    """
    return pd.read_sql_query(query, conn)

# 3. ЗАГРУЗКА ЖУРНАЛА ПЛАНШЕТОВ
@st.cache_data(ttl=600)
def load_shipped_plates():
    query = """
        SELECT DISTINCT ON (scl.container_barcode)
            scl.container_barcode,
            scl.shipped_at,
            ate.clinic_code,
            ate.lab_code
        FROM public.server_shipped_containers_log scl
        LEFT JOIN public.audit_trail_events ate ON scl.session_id = ate.session_id
        WHERE scl.shipped_at >= NOW() - INTERVAL '30 days';
    """
    return pd.read_sql_query(query, conn)

# --- ЗАГРУЗКА ДАННЫХ ---
with st.spinner("Синхронизация данных с PostgreSQL..."):
    try:
        df_structure = load_structure_dict()
        df_events = load_audit_data()
        df_plates = load_shipped_plates()
    except Exception as e:
        st.error(f"Ошибка выполнения SQL-запроса: {e}")
        st.stop()

# --- БОКОВАЯ ПАНЕЛЬ (ФИЛЬТРЫ ИЗ СПРАВОЧНИКОВ) ---
st.sidebar.header("Фильтры")

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# Фильтр Клиник
all_clinics_names = ["Все"] + sorted(df_structure["clinic_name"].unique().tolist())
selected_clinic_name = st.sidebar.selectbox("Выберите клинику:", all_clinics_names)

# Динамический фильтр Лабораторий строго из dict_labs_configs
if selected_clinic_name != "Все":
    # Выбираем clinic_code для фильтрации данных
    selected_clinic_code = df_structure[df_structure["clinic_name"] == selected_clinic_name]["clinic_code"].values[0]
    # Находим доступные лабы для этой клиники из справочника конфигураций
    filtered_labs = df_structure[df_structure["clinic_name"] == selected_clinic_name]["lab_code"].dropna().unique().tolist()
else:
    selected_clinic_code = "Все"
    filtered_labs = df_structure["lab_code"].dropna().unique().tolist()

all_labs_options = ["Все"] + sorted(filtered_labs)
selected_lab = st.sidebar.selectbox("Выберите лабораторию:", all_labs_options)


# --- ФИЛЬТРАЦИЯ ДАННЫХ ДЛЯ ВЫВОДА ---
# 1. Фильтруем события
filtered_events = df_events.copy()
if selected_clinic_code != "Все":
    filtered_events = filtered_events[filtered_events["clinic_code"] == selected_clinic_code]
if selected_lab != "Все":
    filtered_events = filtered_events[filtered_events["Код лаб."] == selected_lab]

# 2. Фильтруем планшеты
filtered_plates = df_plates.copy()
if selected_clinic_code != "Все":
    filtered_plates = filtered_plates[filtered_plates["clinic_code"] == selected_clinic_code]
if selected_lab != "Все":
    filtered_plates = filtered_plates[filtered_plates["lab_code"] == selected_lab]


# --- РАСЧЕТ МЕТРИК ---
total_events = len(filtered_events)
error_events = 0
error_rate = 0.0
active_devices = 0
processed_kits = 0
shipped_plates_count = len(filtered_plates)

if total_events > 0:
    # Ошибки
    error_mask = (
        (filtered_events["Статус синхр."].str.lower() != "success") & (filtered_events["Статус синхр."].notna())
    ) | (filtered_events["Детали ошибки"].notna())
    error_events = int(error_mask.sum())
    error_rate = (error_events / total_events * 100)
    active_devices = filtered_events["S/N устройства"].nunique()

    # Комплекты (исправлено под русские названия колонок)
    kits_df = filtered_events.dropna(subset=["Баркод пробирки", "Крио-баркод"])
    processed_kits = kits_df["Баркод пробирки"].nunique()

# Отрисовка базовых метрик
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(label="Всего операций в логе", value=f"{total_events:,}".replace(",", " "))
with col2:
    st.metric(label="Количество ошибок", value=error_events, delta=f"{error_rate:.1f}% от всех", delta_color="inverse")
with col3:
    st.metric(label="Устройств в работе", value=active_devices)

# Отрисовка производственных метрик
st.markdown("### 📦 Показатели (за 30 дней)")
col_kit1, col_kit2 = st.columns(2)
with col_kit1:
    st.metric(
        label="Обработано комплектов (вакуумная + крио)", 
        value=f"{processed_kits:,}".replace(",", " "),
        help="Количество уникальных вакуумных баркодов, прошедших обработку совместно с криопробирками."
    )
with col_kit2:
    st.metric(
        label="Планшетов передано курьеру", 
        value=f"{shipped_plates_count:,}".replace(",", " "),
        help="Количество уникальных штрихкодов контейнеров из таблицы отгрузок за выбранный период."
    )

st.markdown("---")

# --- ОСНОВНАЯ ТАБЛИЦА С ЖУРНАЛОМ ---
st.subheader(f"Логи операций: {selected_clinic_name} / Лаборатория: {selected_lab}")

if filtered_events.empty:
    st.info("По выбранным критериям за последние 30 дней операций не зарегистрировано.")
else:
    display_df = filtered_events.drop(columns=["clinic_code", "session_id"])
    st.dataframe(
        display_df,
        use_container_width=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", format="%d"),
            "Время": st.column_config.DatetimeColumn("Время события", format="DD.MM.YYYY HH:mm:ss"),
            "Детали ошибки": st.column_config.TextColumn("Детали ошибки", width="large")
        },
        hide_index=True
    )
