import os
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.types import ParseMode
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage

API_TOKEN = os.environ.get("API_TOKEN")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")
CALORIES_API_KEY = os.environ.get("CALORIES_API_KEY")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())

# Структура, в которой будем хранить данные пользователей.
# Ключ: user_id, Значения: dict с пользовательской информацией и статистикой
user_data = {}

# --- Helpers ---


def get_temperature(city: str) -> float:
    """
    Подтягиваем температуру с OpenWeatherMap.
    """
    url = "http://api.weatherapi.com/v1/current.json"
    params = {"q": city, "key": OPENWEATHER_API_KEY}
    response = requests.get(url, params=params)
    if response.status_code == requests.codes.ok:
        return response.json()["current"]["temp_c"]
    else:
        return 10  # Отдаем среднее, если не получилось извлечь данные


def get_calories(query: str) -> float:
    api_url = 'https://api.calorieninjas.com/v1/nutrition?query='
    response = requests.get(api_url + query, headers={'X-Api-Key': CALORIES_API_KEY})
    if response.status_code == requests.codes.ok:
        return response.json()["items"][0]["calories"]
    else:
        return 60  # Отдаем среднее, если не получилось извлечь данные


def calculate_daily_water(user_info: dict) -> float:
    """
    Потребление воды:
      - base: weight * 30 ml
      - +500 ml per 30 min activity
      - +500-1000 ml for hot weather (>25C)
    """
    weight = user_info["weight"]
    activity = user_info["activity"]  # in minutes
    city = user_info["city"]
    temp = get_temperature(city)

    base = weight * 30.0
    extra_activity = (activity // 30) * 500.0
    extra_hot = 0.0
    if temp > 25:
        extra_hot = 500.0 if temp <= 30 else 1000.0

    return base + extra_activity + extra_hot


def calculate_bmr(user_info: dict) -> float:
    """
    Считаем дефолтное потребление калорий человеком, если он просто существует.
    BMR formula (Mifflin-St Jeor style):
      10 * weight + 6.25 * height - 5 * age
    """
    w = user_info["weight"]
    h = user_info["height"]
    a = user_info["age"]
    bmr = 10 * w + 6.25 * h - 5 * a
    # Предполагаем, что "lifestyle" - это фактор от  1.1 (сидячий) to 1.5 (активный).
    # Храним это в user_info["lifestyle_factor"].
    factor = user_info.get("lifestyle_factor", 1.2)
    return bmr * factor


def calculate_workout_calories(training_type: str, minutes: float) -> float:
    """
    Подсчет калорий по виду тренировок. Можно расширять на более нетривиальную логику.
    """
    mapping = {
        "бег": 10.0,
        "прогулка": 5.0,
        "велосипед": 8.0
    }
    ccal_per_min = mapping.get(training_type.lower(), 5.0)
    return ccal_per_min * minutes

# --- COMMAND HANDLERS ---


@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.reply("Привет! Используй /set_profile, чтобы инициализировать свой профиль.")


@dp.message_handler(commands=["set_profile"])
async def cmd_set_profile(message: types.Message):
    user_data[message.from_user.id] = {
        "weight": 0,
        "height": 0,
        "age": 0,
        "activity": 0,
        "lifestyle_factor": 1.2,
        "city": "Moscow",
        "target_calories": 2000,
        "water_drank": 0,
        "calories_consumed": 0,
        "calories_burned": 0
    }
    text = ("Отправь мне следующую информацию о себе, разделенную пробелами:\n"
            "- Вес (кг)\n- Рост (см)\n- Возраст"
            "\n- Целевое кол-во активности в день (в минутах)"
            "\n- Образ жизни (где 1.1 - сидячий, и 1.5 - очень активный)"
            "\n- Город (на английском языке) \n- Целевое кол-во калорий (в день)\n\n"
            "Пример: 70 175 25 30 1.2 Moscow 2000")
    await message.reply(text)


@dp.message_handler(lambda msg: len(msg.text.split()) == 7)
async def handle_profile_data(message: types.Message):
    if message.from_user.id not in user_data:
        return
    w, h, a, act, lf, city, tcal = message.text.split()
    user_info = user_data[message.from_user.id]
    # Update user info
    user_info["weight"] = float(w)
    user_info["height"] = float(h)
    user_info["age"] = float(a)
    user_info["activity"] = float(act)
    user_info["lifestyle_factor"] = float(lf)
    user_info["city"] = city
    user_info["target_calories"] = float(tcal)
    user_info["water_drank"] = 0
    user_info["calories_consumed"] = 0
    user_info["calories_burned"] = 0

    await message.reply(f"Профиль обновлен!\n"
                        f"Целевое кол-во воды в день: {calculate_daily_water(user_info)}\n"
                        f"Целевое кол-во калорий в день: {user_info['target_calories']}")


@dp.message_handler(commands=["log_water"])
async def cmd_log_water(message: types.Message):
    """
    /log_water <Кол-во воды в мл>
    """
    args = message.get_args().split()
    if not args:
        await message.reply("Использование: /log_water 'Кол-во воды в мл'")
        return

    qty = float(args[0])
    user_info = user_data.get(message.from_user.id)
    if not user_info:
        await message.reply("Профиль не найден. Используйте /set_profile для инициализации профиля.")
        return

    user_info["water_drank"] += qty
    daily_need = calculate_daily_water(user_info)
    left = daily_need - user_info["water_drank"]
    if left < 0:
        left = 0

    await message.reply(f"Зафиксировал {qty} мл. Осталось до суточной цели: {left:.1f} мл")


@dp.message_handler(commands=["log_food"])
async def cmd_log_food(message: types.Message):
    """
    /log_food <Продукт>
    Шаг 1: Бот отвечает с информацией ccal/100g из внешнего источника.
    Шаг 2: Пользователь пишет кол-во употребленного продукта (гр) -> Бот считаем тотал калорий.
    """
    args = message.get_args().split()
    if not args:
        await message.reply("Использование: /log_food 'Название продукта (на английском языке)'")
        return

    food_name = " ".join(args)
    user_info = user_data.get(message.from_user.id)
    if not user_info:
        await message.reply("Профиль не найден. Используйте /set_profile для инициализации профиля.")
        return

    ccal_per_100g = get_calories(food_name.lower())

    # Сохраняем информацию, которая понадобится при ответе на следующий вопрос
    user_info["pending_food"] = (food_name, ccal_per_100g)
    await message.reply(f"{food_name.capitalize()} - {ccal_per_100g} ккал на 100г. Как много вы съели (в граммах)?")


@dp.message_handler(lambda msg: msg.from_user.id in user_data and "pending_food" in user_data[msg.from_user.id])
async def handle_food_quantity(message: types.Message):
    qty_str = message.text
    if not qty_str.isdigit():
        await message.reply("Используйте валидное число для кол-ва грамм.")
        return

    user_info = user_data[message.from_user.id]
    food_name, ccal_100g = user_info["pending_food"]
    del user_info["pending_food"]

    qty = float(qty_str)
    ccal_consumed = (ccal_100g / 100.0) * qty
    user_info["calories_consumed"] += ccal_consumed

    await message.reply(f"OK. Зафиксировал {ccal_consumed:.1f} ккал от {food_name}.")


@dp.message_handler(commands=["log_workout"])
async def cmd_log_workout(message: types.Message):
    """
    /log_workout <Тип тренировки> <Кол-во минут, которое длилось тренировка>
    """
    args = message.get_args().split()
    if len(args) < 2:
        await message.reply("Использование: /log_workout 'Тип тренировки' 'Минуты'")
        return

    training_type, mins_str = args[0], args[1]
    if not mins_str.isdigit():
        await message.reply("Минуты должны быть числом.")
        return

    user_info = user_data.get(message.from_user.id)
    if not user_info:
        await message.reply("Профиль не найден. Используйте /set_profile для инициализации профиля.")
        return

    mins = float(mins_str)
    ccal_burned = calculate_workout_calories(training_type, mins)
    user_info["calories_burned"] += ccal_burned

    # Базовая формула: 200мл на 30 минут
    add_water = (mins // 30) * 200
    user_info["water_drank"] += add_water

    await message.reply(
        f"{training_type.capitalize()} {mins} минут = {ccal_burned:.1f} ккал сожжено.\n"
        f"Дополнительно выпейте {add_water} мл воды."
    )


@dp.message_handler(commands=["check_progress"])
async def cmd_check_progress(message: types.Message):
    user_info = user_data.get(message.from_user.id)
    if not user_info:
        await message.reply("Профиль не найден. Используйте /set_profile для инициализации профиля.")
        return

    # Вода
    daily_water_need = calculate_daily_water(user_info)
    water_drank = user_info["water_drank"]
    water_left = max(daily_water_need - water_drank, 0)

    # Калории
    bmr = calculate_bmr(user_info)
    daily_target_cal = user_info["target_calories"]
    net_cal = user_info["calories_consumed"] - user_info["calories_burned"]
    cals_left = daily_target_cal - net_cal

    text = (f"<b>Воды выпито</b>: {water_drank:} мл из {daily_water_need:} мл\n"
            f"Осталось выпить воды для достижения цели: {water_left:} мл\n\n"
            f"<b>Калорий потреблено</b>: {user_info['calories_consumed']:.1f}\n"
            f"<b>Калорий сожжено</b>: {user_info['calories_burned']:.1f}\n"
            f"Баланс по калориям = {net_cal:.1f}\n"
            f"Дневной таргет по калориям = {daily_target_cal:.1f}\n"
            f"Калорий осталось до таргета: {(cals_left - bmr):.1f} (с учетом BMR={bmr})")
    await message.reply(text)

# --- Main ---


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
