import streamlit as st
import psycopg2
import pandas as pd

# Настройки интерфейса
st.set_page_config(page_title="Журнал операций лабораторий", page_icon="🔬", layout="wide")

# Проверка наличия секретов
if "postgres" not in st.secrets or "credentials" not in st.secrets:
    st.error("Ошибка: Файл secrets.toml не найден или заполнен неверно!")
    st.stop()

# Инициализация сессии авторизации
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["username"] = ""
    st.session_state["role"] = ""
    st.session_state["clinic_code"] = ""

# --- ЭКРАН АВТОРИЗАЦИИ ---
if not st.session_state["authenticated"]:
    st.title("🔒 Вход в систему")
    
    with st.form("login_form"):
        username = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        submit = st.form_submit_button("Войти")
        
        if submit:
            creds = st.secrets["credentials"]
            if username in creds:
                # Парсим строку: "password:role:clinic_code"
                stored_pass, role, clinic_code = creds[username].split(":")
                if password == stored_pass:
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = username
                    st.session_state["role"] = role
                    st.session_state["clinic_code"] = clinic_code
                    st.success("Успешный вход!")
                    st.rerun()
                else:
                    st.error("Неверный пароль")
            else:
                st.error("Пользователь не найден")
    st.stop()

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ (Нативный psycopg2) ---
@st.cache_resource
def get_connection():
    return psycopg2.connect(**st.secrets["postgres"])

try:
    conn = get_connection()
except Exception as e:
    st.error(f"Ошибка подключения к базе данных: {e}")
    st.stop()

# Вспомогательная функция для безопасного чтения данных в DataFrame
def safe_query(query, params=None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        data = cur.fetchall()
        return pd.DataFrame(data, columns=columns)

# --- ЗАГРУЗКА СПРАВОЧНИКОВ ---
@st.cache_data(ttl=600)
def load_filters_data(user_role, user_clinic_code):
    # Загрузка структуры клиник и лабораторий
    if user_role == "admin":
        struct_q = """
            SELECT dc.clinic_code, dc.clinic_name, dlc.lab_code 
            FROM public.dict_clinics dc
            LEFT JOIN public.dict_labs_configs dlc ON dc.id = dlc.clinic_id AND dlc.is_active = true
            WHERE dc.is_active = true ORDER BY dc.clinic_name, dlc.lab_code;
        """
        df_struct = safe_query(struct_q)
    else:
        struct_q = """
            SELECT dc.clinic_code, dc.clinic_name, dlc.lab_code 
            FROM public.dict_clinics dc
            LEFT JOIN public.dict_labs_configs dlc ON dc.id = dlc.clinic_id AND dlc.is_active = true
            WHERE dc.is_active = true AND dc.clinic_code = %s ORDER BY dlc.lab_code;
        """
        df_struct = safe_query(struct_q, (user_clinic_code,))
        
    # Загрузка доступных S/N устройств за 30 дней
    if user_role == "admin":
        dev_q = "SELECT DISTINCT device_serial FROM public.audit_trail_events WHERE occurred_at >= NOW() - INTERVAL '30 days';"
        df_devices = safe_query(dev_q)
    else:
        dev_q = "SELECT DISTINCT device_serial FROM public.audit_trail_events WHERE occurred_at >= NOW() - INTERVAL '30 days' AND clinic_code = %s;"
        df_devices = safe_query(dev_q, (user_clinic_code,))
        
    return df_struct, df_devices

df_struct, df_devices = load_filters_data(st.session_state["role"], st.session_state["clinic_code"])

# --- БОКОВАЯ ПАНЕЛЬ И ФИЛЬТРЫ ---
st.sidebar.markdown(f"👤 Юзер: **{st.session_state['username']}** ({st.session_state['role']})")
if st.sidebar.button("🚪 Выйти"):
    st.session_state["authenticated"] = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("Фильтры данных")

if st.sidebar.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()

# Логика фильтра Клиник
if st.session_state["role"] == "admin":
    clinic_names = ["Все"] + sorted(df_struct["clinic_name"].unique().tolist())
    selected_clinic_name = st.sidebar.selectbox("Выберите клинику:", clinic_names)
    if selected_clinic_name != "Все":
        selected_clinic_code = df_struct[df_struct["clinic_name"] == selected_clinic_name]["clinic_code"].values[0]
    else:
        selected_clinic_code = "Все"
else:
    selected_clinic_code = st.session_state["clinic_code"]
    selected_clinic_name = df_struct["clinic_name"].unique()[0]
    st.sidebar.info(f"Клиника: {selected_clinic_name}")

# Логика фильтра Лабораторий
if selected_clinic_code != "Все":
    labs_list = df_struct[df_struct["clinic_code"] == selected_clinic_code]["lab_code"].dropna().unique().tolist()
else:
    labs_list = df_struct["lab_code"].dropna().unique().tolist()
selected_lab = st.sidebar.selectbox("Выберите лабораторию:", ["Все"] + sorted(labs_list))

# Логика фильтра Устройств
devices_list = df_devices["device_serial"].dropna().unique().tolist()
selected_device = st.sidebar.selectbox("Выберите устройство (S/N):", ["Все"] + sorted(devices_list))

# --- ОСНОВНОЙ ЗАПРОС ДАННЫХ (С учетом всех фильтров на уровне SQL) ---
query_main = """
    SELECT 
        ate.id,
        ate.occurred_at as "Время события",
        ate.received_at as "Время синхронизации",
        scl.shipped_at as "Время отгрузки планшета",
        dc.clinic_name as "Клиника",
        ate.lab_code as "Код лаб.",
        ate.device_serial as "S/N устройства",
        ate.session_id as "ID сессии (Комплект)",
        ate.vacuum_barcode_raw as "Баркод пробирки",
        ate.cryo_barcode as "Крио-баркод",
        ate.sync_result as "Статус синхр.",
        ate.error_details as "Детали ошибки"
    FROM public.audit_trail_events ate
    LEFT JOIN public.dict_clinics dc ON ate.clinic_code = dc.clinic_code
    LEFT JOIN public.server_shipped_containers_log scl ON ate.session_id = scl.session_id
    WHERE ate.occurred_at >= NOW() - INTERVAL '30 days'
"""

args = []
if selected_clinic_code != "Все":
    query_main += " AND ate.clinic_code = %s"
    args.append(selected_clinic_code)
if selected_lab != "Все":
    query_main += " AND ate.lab_code = %s"
    args.append(selected_lab)
if selected_device != "Все":
    query_main += " AND ate.device_serial = %s"
    args.append(selected_device)

query_main += " ORDER BY ate.session_id, ate.occurred_at DESC;"

with st.spinner("Сборка аналитики..."):
    df_main = safe_query(query_main, tuple(args))

# --- РАСЧЕТ МЕТРИК ---
st.title(f"🔬 Мониторинг Биобанка: {selected_clinic_name}")

total_events = len(df_main)
error_events = 0
error_rate = 0.0
processed_kits = 0
shipped_plates = 0

if total_events > 0:
    # Подсчет ошибок
    error_mask = (df_main["Статус синхр."].str.lower() != "success") & (df_main["Статус синхр."].notna()) | (df_main["Детали ошибки"].notna())
    error_events = int(error_mask.sum())
    error_rate = (error_events / total_events) * 100
    
    # Комплекты (Уникальные вакуумные пробирки)
    processed_kits = df_main["Баркод пробирки"].dropna().nunique()
    
    # Планшеты (Уникальные даты/время отгрузок)
    shipped_plates = df_main["Время отгрузки планшета"].dropna().nunique()

# Отрисовка карточек
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Всего операций", f"{total_events:,}".replace(",", " "))
with col2:
    st.metric("Количество ошибок", error_events, delta=f"{error_rate:.1f}%", delta_color="inverse")
with col3:
    st.metric("Обработано комплектов", processed_kits)
with col4:
    st.metric("Планшетов отгружено", shipped_plates)

st.markdown("---")

# --- ВИЗУАЛИЗАЦИЯ И ТАБЛИЦА ЛОГОВ ---
st.subheader("📋 Детальный журнал операций (Группировка по комплектам)")

if df_main.empty:
    st.info("За последние 30 дней операций по выбранным фильтрам не найдено.")
else:
    # Стилизация таблицы: поочередное окрашивание групп комплектов (session_id)
    # Создаем маску для визуального разделения смежных комплектов
    unique_sessions = df_main["ID сессии (Комплект)"].dropna().unique()
    session_color_map = {session: "background-color: #f9f9f9" if i % 2 == 0 else "background-color: #ffffff" for i, session in enumerate(unique_sessions)}
    
    def style_rows(row):
        return [session_color_map.get(row["ID сессии (Комплект)"], "")] * len(row)
        
    styled_df = df_main.style.apply(style_rows, axis=1)
    
    # Вывод интерактивной таблицы
    st.dataframe(
        styled_df,
        width="stretch",
        column_config={
            "id": st.column_config.NumberColumn("ID", format="%d"),
            "Время события": st.column_config.DatetimeColumn("Время события", format="DD.MM.YYYY HH:mm:ss"),
            "Время синхронизации": st.column_config.DatetimeColumn("Синхронизировано", format="DD.MM.YYYY HH:mm:ss"),
            "Время отгрузки планшета": st.column_config.DatetimeColumn("Отгрузка курьеру", format="DD.MM.YYYY HH:mm:ss"),
        },
        hide_index=True
    )
