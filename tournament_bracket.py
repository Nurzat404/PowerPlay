"""
Генератор турнирных сеток (bracket) для олимпийской системы (плей-офф).
Поддерживает нечётное количество участников с автоматическим добавлением "BYE".

Библиотеки: Pillow (PIL), math
"""

import math
from PIL import Image, ImageDraw, ImageFont
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# НАСТРОЙКИ (все параметры вынесены для удобной настройки)
# =============================================================================

# Размеры блоков и отступы
BOX_W = 220           # Ширина блока команды (пиксели)
BOX_H = 60            # Высота блока команды (пиксели)
RADIUS = 12           # Радиус скругления углов блока
# Отступ между двумя командами в одной паре (по вертикали)
PAIR_GAP = 20
# Отступ между разными парами на одном этапе (по вертикали)
STAGE_GAP = 80
STAGE_OFFSET_X = 280  # Горизонтальное расстояние между этапами

# Координатная сетка
START_X = 100         # Отступ левого края первого этапа от края холста
START_Y = 150         # Отступ верхнего края от края холста
TITLE_OFFSET_Y = 30   # Расстояние названий этапов от верхнего блока раунда

# Линии
LINE_WIDTH = 3        # Толщина соединительных линий
LINE_MID_OFFSET = 40  # Отступ для излома линии (вертикальная часть)

# Шрифты
FONT_SIZE_TEAM = 20   # Размер шрифта для названий команд
FONT_SIZE_ROUND = 24  # Размер шрифта для названий этапов
FONT_SIZE_TITLE = 36  # Размер шрифта для заголовка турнира

# Цвета (RGB)
BACKGROUND_TOP = (30, 30, 46)      # #1e1e2e - верх градиента фона
BACKGROUND_BOTTOM = (10, 10, 15)   # #0a0a0f - низ градиента фона
BOX_COLOR = (44, 44, 58)           # #2c2c3a - цвет блоков команд
BOX_BYE_COLOR = (35, 35, 45)       # Затемнённый цвет для BYE
SHADOW_COLOR = (0, 0, 0, 60)       # Полупрозрачная тень
LINE_COLOR = (201, 160, 61)        # #c9a03d - золотистый цвет линий
TEXT_COLOR = (255, 255, 255)       # Белый цвет текста команд
ROUND_TEXT_COLOR = (212, 175, 55)  # #d4af37 - золотистый для названий этапов
TITLE_COLOR = (255, 255, 255)      # Белый для заголовка

# Прочее
DPI = 150             # Разрешение изображения
SHADOW_OFFSET = 4     # Смещение тени блоков


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def get_font(size: int, bold: bool = False, italic: bool = False):
    """
    Получение шрифта заданного размера.
    Пытается загрузить Arial, при неудаче использует дефолтный.
    """
    font_name = "arial.ttf"
    if bold:
        font_name = "arialbd.ttf"
    elif italic:
        font_name = "ariali.ttf"

    try:
        return ImageFont.truetype(font_name, size)
    except (IOError, OSError):
        # Если Arial недоступен, пробуем другие варианты
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except (IOError, OSError):
            return ImageFont.load_default()


def next_power_of_two(n: int) -> int:
    """
    Возвращает ближайшую степень двойки, большую или равную n.
    """
    if n <= 1:
        return 2
    return 2 ** math.ceil(math.log2(n))


def create_gradient_background(width: int, height: int,
                               color_top: tuple, color_bottom: tuple) -> Image:
    """
    Создаёт изображение с вертикальным градиентным фоном.
    """
    img = Image.new('RGB', (width, height), color_bottom)
    draw = ImageDraw.Draw(img)

    # Рисуем градиент построчно
    for y in range(height):
        # Коэффициент интерполяции (0 вверху, 1 внизу)
        ratio = y / height
        r = int(color_top[0] * (1 - ratio) + color_bottom[0] * ratio)
        g = int(color_top[1] * (1 - ratio) + color_bottom[1] * ratio)
        b = int(color_top[2] * (1 - ratio) + color_bottom[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    return img


def draw_rounded_rectangle(draw: ImageDraw, x: int, y: int, w: int, h: int,
                           radius: int, fill: tuple, shadow: bool = False,
                           image: Image = None):
    """
    Рисует прямоугольник с закруглёнными углами.
    Опционально добавляет тень.

    Args:
        draw: объект ImageDraw
        x, y: координаты левого верхнего угла
        w, h: размеры прямоугольника
        radius: радиус скругления
        fill: цвет заливки (RGB)
        shadow: рисовать ли тень
        image: основное изображение (нужно для отрисовки тени)
    """
    if shadow and image is not None:
        # Рисуем тень (смещённый полупрозрачный прямоугольник)
        shadow_size = (w + SHADOW_OFFSET * 2, h + SHADOW_OFFSET * 2)
        shadow_img = Image.new('RGBA', shadow_size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)

        # Рисуем закруглённый прямоугольник тени со смещением
        _draw_rounded_rect_shadow(shadow_draw, SHADOW_OFFSET, SHADOW_OFFSET,
                                  w, h, radius)

        # Накладываем тень на основное изображение через alpha_composite
        image.paste(shadow_img, (x - SHADOW_OFFSET,
                    y - SHADOW_OFFSET), shadow_img)

    # Основной прямоугольник
    _draw_rounded_rect_path(draw, x, y, w, h, radius, fill)


def _draw_rounded_rect_shadow(draw: ImageDraw, x: int, y: int, w: int, h: int,
                              radius: int):
    """
    Рисует закруглённый прямоугольник для тени (полупрозрачный чёрный).
    """
    left = x
    right = x + w
    top = y
    bottom = y + h

    # Рисуем центральную часть
    draw.rectangle([left + radius, top, right -
                   radius, bottom], fill=SHADOW_COLOR)
    draw.rectangle([left, top + radius, right,
                   bottom - radius], fill=SHADOW_COLOR)

    # Рисуем 4 закруглённых угла
    draw.pieslice([left, top, left + 2*radius, top + 2*radius],
                  start=180, end=270, fill=SHADOW_COLOR)
    draw.pieslice([right - 2*radius, top, right, top + 2*radius],
                  start=270, end=360, fill=SHADOW_COLOR)
    draw.pieslice([left, bottom - 2*radius, left + 2*radius, bottom],
                  start=90, end=180, fill=SHADOW_COLOR)
    draw.pieslice([right - 2*radius, bottom - 2*radius, right, bottom],
                  start=0, end=90, fill=SHADOW_COLOR)


def _draw_rounded_rect_path(draw: ImageDraw, x: int, y: int, w: int, h: int,
                            radius: int, fill: tuple):
    """
    Внутренняя функция для рисования закруглённого прямоугольника.
    """
    # Координаты углов
    left = x
    right = x + w
    top = y
    bottom = y + h

    # Рисуем центральную часть (прямоугольник без углов)
    draw.rectangle([left + radius, top, right - radius, bottom], fill=fill)
    draw.rectangle([left, top + radius, right, bottom - radius], fill=fill)

    # Рисуем 4 закруглённых угла (четверти круга)
    # Левый верхний
    draw.pieslice([left, top, left + 2*radius, top + 2*radius],
                  start=180, end=270, fill=fill)
    # Правый верхний
    draw.pieslice([right - 2*radius, top, right, top + 2*radius],
                  start=270, end=360, fill=fill)
    # Левый нижний
    draw.pieslice([left, bottom - 2*radius, left + 2*radius, bottom],
                  start=90, end=180, fill=fill)
    # Правый нижний
    draw.pieslice([right - 2*radius, bottom - 2*radius, right, bottom],
                  start=0, end=90, fill=fill)


def draw_connector_line(draw: ImageDraw,
                        start_pos: tuple, end_pos: tuple,
                        mid_x: int, color: tuple, width: int):
    """
    Рисует строго прямоугольную (ортогональную) соединительную линию.

    Алгоритм:
    1. Горизонтальный отрезок от start до x = mid_x
    2. Вертикальный отрезок на x = mid_x до уровня end_y
    3. Горизонтальный отрезок от mid_x до end_pos

    start_pos: (x, y) - правый центр блока команды
    end_pos: (x, y) - левый центр блока следующего раунда
    mid_x: X-координата вертикального сегмента
    """
    x1, y1 = start_pos
    x2, y2 = end_pos

    # Точки излома
    mid_y = y2  # Уровень целевого блока

    # Рисуем три сегмента
    # 1. Горизонтальный от start до mid_x
    draw.line([(x1, y1), (mid_x, y1)], fill=color, width=width)
    # 2. Вертикальный от (mid_x, y1) до (mid_x, mid_y)
    draw.line([(mid_x, y1), (mid_x, mid_y)], fill=color, width=width)
    # 3. Горизонтальный от mid_x до end
    draw.line([(mid_x, mid_y), (x2, mid_y)], fill=color, width=width)


def draw_pair_connector(draw: ImageDraw,
                        top_start: tuple, bottom_start: tuple,
                        end_pos: tuple, color: tuple, width: int):
    """
    Рисует соединительные линии от двух блоков пары к одному блоку следующего раунда.

    Алгоритм:
    1. От каждого блока горизонтальная линия до общей вертикали (mid_x)
    2. Вертикальная линия от верхнего блока до нижнего (или до уровня целевого блока)
    3. От середины вертикали горизонтальная линия к целевому блоку
    """
    x1_top, y1_top = top_start
    x1_bottom, y1_bottom = bottom_start
    x2, y2 = end_pos

    # Общая вертикальная линия на расстоянии LINE_MID_OFFSET от целевого блока
    mid_x = x2 - LINE_MID_OFFSET

    # Линия от верхнего блока до вертикали
    draw.line([(x1_top, y1_top), (mid_x, y1_top)], fill=color, width=width)
    # Линия от нижнего блока до вертикали
    draw.line([(x1_bottom, y1_bottom), (mid_x, y1_bottom)],
              fill=color, width=width)

    # Вертикальная соединительная линия - должна покрывать весь диапазон
    # от минимального Y (верхний блок или целевой) до максимального Y
    vert_top = min(y1_top, y2)
    vert_bottom = max(y1_bottom, y2)
    draw.line([(mid_x, vert_top), (mid_x, vert_bottom)],
              fill=color, width=width)

    # Горизонтальная линия от вертикали к целевому блоку (на уровне y2)
    draw.line([(mid_x, y2), (x2, y2)], fill=color, width=width)


# =============================================================================
# ЛОГИКА ПОСТРОЕНИЯ ТУРНИРНОЙ СЕТКИ
# =============================================================================

class TeamSlot:
    """
    Класс, представляющий слот команды в сетке.
    """

    def __init__(self, name: str, is_real: bool = True):
        self.name = name
        self.is_real = is_real  # False для BYE

    def __repr__(self):
        return f"TeamSlot({self.name}, real={self.is_real})"


def build_bracket(teams: list) -> list:
    """
    Построение полной турнирной сетки.

    Возвращает список раундов, где каждый раунд - это список пар.

    ВАЖНО: Используем порядок команд ИЗ СПИСКА (без перемешивания)
    """
    n_teams = len(teams)
    total_teams = next_power_of_two(n_teams)
    n_byes = total_teams - n_teams

    # Создаём список слотов
    slots = [TeamSlot(team, is_real=True) for team in teams]
    for _ in range(n_byes):
        slots.append(TeamSlot("BYE", is_real=False))

    # Используем порядок из списка (без перемешивания!)
    ordered_slots = slots.copy()

    # Формируем пары первого раунда
    rounds = []
    round_pairs = []
    for i in range(0, total_teams, 2):
        pair = (ordered_slots[i], ordered_slots[i + 1])
        round_pairs.append(pair)
    rounds.append(round_pairs)

    # Создаём пустые раунды для победителей
    current_pairs = total_teams // 2
    while current_pairs > 1:
        next_round = [(None, None) for _ in range(current_pairs // 2)]
        rounds.append(next_round)
        current_pairs //= 2

    return rounds


def simulate_tournament(rounds: list, db_matches: list = None) -> list:
    """
    Симуляция прохождения турнира для определения, какие команды
    проходят в следующие раунды.

    Если db_matches предоставлен, использует данные из БД для заполнения победителей.

    Возвращает (winners, result_rounds) где winners содержит победителей для следующих раундов.
    """
    if db_matches is None:
        # Старое поведение - не заполнять победителей
        winners = [[] for _ in range(len(rounds) - 1)]
        result_rounds = [round_pairs[:] for round_pairs in rounds]
        return winners, result_rounds

    # Группируем матчи из БД по раундам
    db_matches_by_round = {}
    for match in db_matches:
        round_num = match['round_number']
        if round_num not in db_matches_by_round:
            db_matches_by_round[round_num] = []
        db_matches_by_round[round_num].append(match)

    def make_team_slot(match, team_num: int):
        team_id_key = f"team{team_num}_id"
        team_name_key = f"team{team_num}_name"
        team_id = match[team_id_key]
        team_name = match[team_name_key] or "???"

        if team_id:
            if match['winner_id'] == team_id:
                team_name = f"[W] {team_name}"
            return TeamSlot(team_name, is_real=True)

        # В BYE-матчах пустой слот отображаем как BYE в старом стиле.
        if match['is_bye']:
            return TeamSlot("BYE", is_real=False)

        return TeamSlot("TBD", is_real=True)

    # Заполняем победителей из БД
    winners = []
    result_rounds = [round_pairs[:] for round_pairs in rounds]

    # Первый раунд берём напрямую из БД, чтобы сохранить точные пары
    # при любом распределении BYE.
    first_round_db_matches = db_matches_by_round.get(1, [])
    if first_round_db_matches:
        first_round_db_matches = sorted(
            first_round_db_matches, key=lambda m: m['match_number']
        )
        result_rounds[0] = [
            (make_team_slot(match, 1), make_team_slot(match, 2))
            for match in first_round_db_matches
        ]

    for r in range(len(rounds) - 1):
        round_winners = []
        prev_round_matches = db_matches_by_round.get(
            r + 1, [])  # Раунды в БД с 1

        for match in prev_round_matches:
            if match['winner_id']:
                # Определяем имя победителя
                if match['winner_id'] == match['team1_id'] and match['team1_name']:
                    winner_name = f"[W] {match['team1_name']}"
                elif match['winner_id'] == match['team2_id'] and match['team2_name']:
                    winner_name = f"[W] {match['team2_name']}"
                else:
                    winner_name = "???"
                round_winners.append(TeamSlot(winner_name, is_real=True))
            else:
                round_winners.append(None)

        winners.append(round_winners)

    # Заполняем следующие раунды победителями
    # Используем db_matches для получения информации о матчах
    db_matches_by_round = {}
    for match in db_matches:
        round_num = match['round_number']
        if round_num not in db_matches_by_round:
            db_matches_by_round[round_num] = []
        db_matches_by_round[round_num].append(match)

    for r in range(1, len(result_rounds)):
        # Получаем матчи ТЕКУЩЕГО раунда из БД (не предыдущего!)
        current_round_db_matches = db_matches_by_round.get(r + 1, [])

        for i, pair in enumerate(result_rounds[r]):
            if pair[0] is None and i < len(current_round_db_matches):
                # Берём данные из матча текущего раунда
                db_match = current_round_db_matches[i]

                # Команда 1: если team1_id установлен - берём название
                team1 = TeamSlot("TBD", is_real=True)
                if db_match['team1_id']:
                    team1_name = db_match['team1_name'] or "???"
                    # Проверяем есть ли winner_id для этого матча
                    if db_match['winner_id'] == db_match['team1_id']:
                        team1 = TeamSlot(
                            f"[W] {team1_name}", is_real=True)
                    else:
                        team1 = TeamSlot(team1_name, is_real=True)

                # Команда 2: если team2_id установлен - берём название
                team2 = TeamSlot("TBD", is_real=True)
                if db_match['team2_id']:
                    team2_name = db_match['team2_name'] or "???"
                    # Проверяем есть ли winner_id для этого матча
                    if db_match['winner_id'] == db_match['team2_id']:
                        team2 = TeamSlot(
                            f"[W] {team2_name}", is_real=True)
                    else:
                        team2 = TeamSlot(team2_name, is_real=True)

                result_rounds[r][i] = (team1, team2)

    return winners, result_rounds


def get_round_for_display(rounds: list, winners: list, r: int) -> list:
    """
    Возвращает список пар для отображения раунда r.
    Для первого раунда (r=0) возвращает оригинальные пары.
    Для последующих раундов формирует пары из победителей предыдущего раунда.
    """
    if r == 0:
        return rounds[0]

    # Для раунда r берём победителей раунда r-1 и формируем пары
    # winners[r-1] содержит список победителей из предыдущего раунда
    # Эти победители должны быть сгруппированы в пары для текущего раунда
    prev_winners = winners[r - 1]

    # Фильтруем None и формируем пары
    # winners[r-1] содержит всех победителей предыдущего раунда подряд
    # Для раунда 1 (полуфинал) нам нужны победители из раунда 0 (1/4 финала)
    # Они группируются: (winners[0], winners[1]) -> пара 0, (winners[2], winners[3]) -> пара 1

    # Но структура winners может содержать None для пустых пар
    # Поэтому берём только реальных победителей
    real_winners = [w for w in prev_winners if w is not None]

    display_round = []
    for i in range(0, len(real_winners), 2):
        team1 = real_winners[i] if i < len(real_winners) else None
        team2 = real_winners[i + 1] if i + 1 < len(real_winners) else None
        display_round.append((team1, team2))

    # Если в original_rounds[r] есть дополнительные пустые пары, добавляем их
    if r < len(rounds):
        original_pairs = len(rounds[r])
        while len(display_round) < original_pairs:
            display_round.append((None, None))

    return display_round


# =============================================================================
# ОТРИСОВКА ТУРНИРНОЙ СЕТКИ
# =============================================================================

def calculate_round_positions(rounds: list, canvas_height: int) -> list:
    """
    Расчёт вертикальных позиций для каждого раунда.

    Возвращает список кортежей (start_y, pair_positions) для каждого раунда,
    где pair_positions - список кортежей (y_top, y_bottom) для каждой пары.
    """
    positions = []

    for round_idx, round_pairs in enumerate(rounds):
        num_pairs = len(round_pairs)

        # Общая высота, занимаемая этим раундом
        # Высота одной пары = 2 * BOX_H + PAIR_GAP
        # Отступ между парами = STAGE_GAP
        pair_height = 2 * BOX_H + PAIR_GAP
        total_height = pair_height * num_pairs + STAGE_GAP * (num_pairs - 1)

        # Стартовая Y-координата для центрирования
        start_y = (canvas_height - total_height) // 2

        # Позиции для каждой пары
        pair_positions = []
        for i in range(num_pairs):
            y_offset = i * (pair_height + STAGE_GAP)
            y_top = start_y + y_offset
            y_bottom = y_top + BOX_H + PAIR_GAP
            pair_positions.append((y_top, y_bottom))

        positions.append((start_y, pair_positions))

    return positions


def draw_tournament_bracket(teams: list, tournament_name: str, round_names: list, output_path: str = "tournament_bracket.png", db_matches: list = None):
    """
    Основная функция отрисовки турнирной сетки.

    Args:
        teams: Список названий команд (строки)
        tournament_name: Название турнира для заголовка
        round_names: Список названий раундов (будет обрезан/дополнен при необходимости)
        output_path: Путь для сохранения файла (по умолчанию tournament_bracket.png)
        db_matches: Данные матчей из БД для заполнения победителей (опционально)

    Generates:
        PNG файл с турнирной сеткой
    """
    # -------------------------------------------------------------------------
    # 1. Построение сетки
    # -------------------------------------------------------------------------
    original_rounds = build_bracket(teams)
    winners, updated_rounds = simulate_tournament(original_rounds, db_matches)

    n_rounds = len(original_rounds)

    # -------------------------------------------------------------------------
    # 2. Расчёт размеров холста
    # -------------------------------------------------------------------------
    # Для расчёта высоты используем количество пар в каждом раунде
    # Раунд 0: original_rounds[0], раунд r>0: используем updated_rounds
    def get_num_pairs(r):
        return len(updated_rounds[r])

    canvas_width = START_X + STAGE_OFFSET_X * (n_rounds - 1) + BOX_W + 100
    canvas_height = START_Y * 2 + max(
        (2 * BOX_H + PAIR_GAP) * get_num_pairs(r) +
        STAGE_GAP * (get_num_pairs(r) - 1)
        for r in range(n_rounds)
    )

    # -------------------------------------------------------------------------
    # 3. Создание изображения с градиентным фоном
    # -------------------------------------------------------------------------
    image = create_gradient_background(canvas_width, canvas_height,
                                       BACKGROUND_TOP, BACKGROUND_BOTTOM)
    draw = ImageDraw.Draw(image)

    # -------------------------------------------------------------------------
    # 4. Загрузка шрифтов
    # -------------------------------------------------------------------------
    font_team = get_font(FONT_SIZE_TEAM)
    font_round = get_font(FONT_SIZE_ROUND, bold=True)
    font_title = get_font(FONT_SIZE_TITLE, bold=True)
    font_bye = get_font(FONT_SIZE_TEAM, italic=True)

    # -------------------------------------------------------------------------
    # 5. Отрисовка заголовка турнира
    # -------------------------------------------------------------------------
    title_bbox = draw.textbbox((0, 0), tournament_name, font=font_title)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (canvas_width - title_width) // 2
    title_y = 40
    draw.text((title_x, title_y), tournament_name,
              fill=TITLE_COLOR, font=font_title)

    # Декоративная линия под заголовком
    line_y = title_y + FONT_SIZE_TITLE + 15
    draw.line([(50, line_y), (canvas_width - 50, line_y)],
              fill=LINE_COLOR, width=2)

    # -------------------------------------------------------------------------
    # 6. Расчёт позиций раундов
    # -------------------------------------------------------------------------
    # Рассчитываем позиции для каждого раунда
    # Важно: используем единое центрирование для всех раундов на основе
    # максимального количества пар (первый раунд)

    # Первый раунд имеет максимальное количество пар
    max_pairs = get_num_pairs(0)
    pair_height = 2 * BOX_H + PAIR_GAP
    pair_step = pair_height + STAGE_GAP  # Шаг между парами

    # Общая высота для максимального количества пар
    max_total_height = pair_step * max_pairs - \
        STAGE_GAP  # Последняя пара не имеет отступа после
    # Базовая Y-координата (верх первого раунда)
    base_start_y = (canvas_height - max_total_height) // 2

    round_positions = []
    for r in range(n_rounds):
        num_pairs = get_num_pairs(r)

        # Для раунда 0 используем базовую позицию
        # Для раунда r > 0, каждый блок соответствует 2^r парам раунда 0
        # и должен быть позиционирован посередине соответствующей группы

        pair_positions = []
        for i in range(num_pairs):
            # Для раунда r, блок i соответствует парам с индексами
            # от i * 2^r до (i+1) * 2^r - 1 раунда 0
            # Например, для r=1, i=0: пары 0 и 1 раунда 0
            # Для r=1, i=1: пары 2 и 3 раунда 0

            groups_per_block = 2 ** r  # Количество пар раунда 0 на один блок раунда r

            # Индекс первой пары раунда 0, соответствующей этому блоку
            first_pair_idx = i * groups_per_block

            # Индекс последней пары раунда 0, соответствующей этому блоку
            last_pair_idx = first_pair_idx + groups_per_block - 1

            # Y-координата середины группы пар
            # y_top первой пары: base_start_y + first_pair_idx * pair_step
            # y_top последней пары: base_start_y + last_pair_idx * pair_step
            # Середина: (y_top_first + y_top_last) / 2 + BOX_H / 2 + PAIR_GAP / 2

            # Но нам нужно y_top и y_bottom для блока
            # y_top блока = середина группы - BOX_H / 2 - PAIR_GAP / 2
            # y_bottom блока = середина группы + BOX_H / 2 + PAIR_GAP / 2

            y_top_first = base_start_y + first_pair_idx * pair_step
            y_top_last = base_start_y + last_pair_idx * pair_step

            # Середина между верхними краями первой и последней пары
            group_center_y = (y_top_first + y_top_last) / 2 + pair_height / 2

            # y_top и y_bottom для блока
            y_top = int(group_center_y - BOX_H / 2 - PAIR_GAP / 2)
            y_bottom = int(group_center_y + BOX_H / 2 + PAIR_GAP / 2)

            pair_positions.append((y_top, y_bottom))

        round_positions.append(
            (pair_positions[0][0] if pair_positions else base_start_y, pair_positions))

    # -------------------------------------------------------------------------
    # 7. Отрисовка раундов и команд
    # -------------------------------------------------------------------------
    # Храним координаты блоков для рисования линий
    # block_coords[round][pair_index] = (top_rect, bottom_rect)
    block_coords = []

    for r in range(n_rounds):
        # Используем обновлённые раунды с победителями
        round_pairs = updated_rounds[r]

        round_x = START_X + r * STAGE_OFFSET_X
        start_y, pair_positions = round_positions[r]

        # Название раунда
        round_name = round_names[r] if r < len(
            round_names) else f"Раунд {r + 1}"
        name_bbox = draw.textbbox((0, 0), round_name, font=font_round)
        name_width = name_bbox[2] - name_bbox[0]
        name_x = round_x + (BOX_W - name_width) // 2
        name_y = start_y - TITLE_OFFSET_Y - FONT_SIZE_ROUND
        draw.text((name_x, name_y), round_name,
                  fill=ROUND_TEXT_COLOR, font=font_round)

        round_blocks = []

        for i, (y_top, y_bottom) in enumerate(pair_positions):
            if i >= len(round_pairs):
                # Нет пары для этой позиции - добавляем пустые координаты
                top_right = (round_x + BOX_W, y_top + BOX_H // 2)
                bottom_right = (round_x + BOX_W, y_bottom + BOX_H // 2)
                round_blocks.append(top_right)
                round_blocks.append(bottom_right)
                # Рисуем пустые блоки
                draw_rounded_rectangle(draw, round_x, y_top, BOX_W, BOX_H,
                                       RADIUS, BOX_COLOR, shadow=True, image=image)
                draw_rounded_rectangle(draw, round_x, y_bottom, BOX_W, BOX_H,
                                       RADIUS, BOX_COLOR, shadow=True, image=image)
                continue

            pair = round_pairs[i]

            # Обрабатываем пару (может быть None или кортежем)
            if pair is None or not isinstance(pair, tuple):
                top_right = (round_x + BOX_W, y_top + BOX_H // 2)
                bottom_right = (round_x + BOX_W, y_bottom + BOX_H // 2)
                round_blocks.append(top_right)
                round_blocks.append(bottom_right)
                continue

            team1, team2 = pair

            # Пропускаем, если оба None
            if team1 is None and team2 is None:
                # Пустой блок - рисуем заглушку
                top_right = (round_x + BOX_W, y_top + BOX_H // 2)
                bottom_right = (round_x + BOX_W, y_bottom + BOX_H // 2)
                round_blocks.append(top_right)
                round_blocks.append(bottom_right)
                draw_rounded_rectangle(draw, round_x, y_top, BOX_W, BOX_H,
                                       RADIUS, BOX_COLOR, shadow=True, image=image)
                draw_rounded_rectangle(draw, round_x, y_bottom, BOX_W, BOX_H,
                                       RADIUS, BOX_COLOR, shadow=True, image=image)
                continue

            # Определяем цвет и шрифт для каждой команды
            # BYE отображаем затемнённым и курсивом
            if team1 is not None:
                box1_color = BOX_COLOR if team1.is_real else BOX_BYE_COLOR
                font1 = font_team if team1.is_real else font_bye
                text1_color = TEXT_COLOR if team1.is_real else (150, 150, 150)
                team1_name = team1.name
            else:
                box1_color = BOX_BYE_COLOR
                font1 = font_bye
                text1_color = (150, 150, 150)
                team1_name = "BYE"

            if team2 is not None:
                box2_color = BOX_COLOR if team2.is_real else BOX_BYE_COLOR
                font2 = font_team if team2.is_real else font_bye
                text2_color = TEXT_COLOR if team2.is_real else (150, 150, 150)
                team2_name = team2.name
            else:
                box2_color = BOX_BYE_COLOR
                font2 = font_bye
                text2_color = (150, 150, 150)
                team2_name = "BYE"

            # Рисуем блоки команд (с тенью)
            draw_rounded_rectangle(draw, round_x, y_top, BOX_W, BOX_H,
                                   RADIUS, box1_color, shadow=True, image=image)
            draw_rounded_rectangle(draw, round_x, y_bottom, BOX_W, BOX_H,
                                   RADIUS, box2_color, shadow=True, image=image)

            # Текст команд (центрированный по вертикали в блоке)
            text1_bbox = draw.textbbox((0, 0), team1_name, font=font1)
            text1_width = text1_bbox[2] - text1_bbox[0]
            text1_x = round_x + (BOX_W - text1_width) // 2
            text1_y = y_top + (BOX_H - FONT_SIZE_TEAM) // 2 - 2

            text2_bbox = draw.textbbox((0, 0), team2_name, font=font2)
            text2_width = text2_bbox[2] - text2_bbox[0]
            text2_x = round_x + (BOX_W - text2_width) // 2
            text2_y = y_bottom + (BOX_H - FONT_SIZE_TEAM) // 2 - 2

            draw.text((text1_x, text1_y), team1_name,
                      fill=text1_color, font=font1)
            draw.text((text2_x, text2_y), team2_name,
                      fill=text2_color, font=font2)

            # Сохраняем координаты для линий
            # Правый центр каждого блока (отдельно для каждого блока)
            top_right = (round_x + BOX_W, y_top + BOX_H // 2)
            bottom_right = (round_x + BOX_W, y_bottom + BOX_H // 2)
            round_blocks.append(top_right)
            round_blocks.append(bottom_right)

        block_coords.append(round_blocks)

    # -------------------------------------------------------------------------
    # 8. Отрисовка соединительных линий (после блоков, чтобы не перекрывать текст)
    # -------------------------------------------------------------------------
    # Логика:
    # - Каждая пара блоков (2 блока) соединяется с ОДНИМ блоком следующего раунда
    # - Пары 0,1 → Блок 0 следующего раунда
    # - Пары 2,3 → Блок 1 следующего раунда
    # - и т.д.
    #
    # Для этого:
    # 1. Рисуем "вилку" от пары блоков к общей вертикальной линии
    # 2. От середины вертикали рисуем горизонтальную линию к блоку следующего раунда

    for r in range(n_rounds - 1):
        if r >= len(block_coords):
            continue
        current_blocks = block_coords[r]
        next_round_x = START_X + (r + 1) * STAGE_OFFSET_X

        # Получаем позиции следующего раунда
        _, next_pair_positions = round_positions[r + 1]

        # Обрабатываем каждую пару блоков (2 блока = 1 пара текущего раунда)
        # Пара i соединяется с блоком (i // 2) следующего раунда
        for pair_idx in range(0, len(current_blocks), 2):
            if pair_idx + 1 >= len(current_blocks):
                continue  # Неполная пара

            top_right = current_blocks[pair_idx]
            bottom_right = current_blocks[pair_idx + 1]

            # Определяем, с каким блоком следующего раунда соединяется эта пара
            target_block_idx = pair_idx // 2
            target_pair_idx = target_block_idx // 2  # Индекс пары в следующем раунде
            is_upper_block = (target_block_idx %
                              2) == 0  # Верхний или нижний блок

            if target_pair_idx >= len(next_pair_positions):
                continue

            # Получаем Y-координату целевого блока
            next_y_top, next_y_bottom = next_pair_positions[target_pair_idx]
            target_y = next_y_top if is_upper_block else next_y_bottom
            target_center = (next_round_x, target_y + BOX_H // 2)

            # Рисуем "вилку" от пары к целевому блоку
            draw_pair_connector(draw, top_right, bottom_right,
                                target_center, LINE_COLOR, LINE_WIDTH)

    # -------------------------------------------------------------------------
    # 9. Декоративная рамка по краям
    # -------------------------------------------------------------------------
    border_margin = 10
    draw.rectangle([border_margin, border_margin,
                    canvas_width - border_margin, canvas_height - border_margin],
                   outline=LINE_COLOR, width=2)

    # -------------------------------------------------------------------------
    # 10. Сохранение изображения
    # -------------------------------------------------------------------------
    image.save(output_path, dpi=(DPI, DPI))
    logger.info(f"Турнирная сетка сохранена в {output_path}")
    logger.info(f"Размер холста: {canvas_width}x{canvas_height} пикселей")
    logger.info(f"Количество раундов: {n_rounds}")
    logger.info(f"Команд: {len(teams)}, с BYE: {next_power_of_two(len(teams))}")


# =============================================================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# =============================================================================

if __name__ == "__main__":
    # -------------------------------------------------------------------------
    # Пример 1: 5 команд (нечётное количество, будут добавлены BYE)
    # 5 команд -> 8 слотов -> 3 раунда (1/4 финала, Полуфинал, Финал)
    # -------------------------------------------------------------------------
    teams_5 = ["Команда A", "Команда B", "Команда C", "Команда D", "Команда E"]
    tournament_name_5 = "Чемпионат по программированию (5 команд)"
    round_names_5 = ["1/4 финала", "Полуфинал", "Финал"]

    draw_tournament_bracket(teams_5, tournament_name_5, round_names_5)
    logger.info("\n" + "="*60 + "\n")

    # -------------------------------------------------------------------------
    # Пример 2: 8 команд (полная сетка без BYE)
    # 8 команд -> 3 раунда (1/4 финала, Полуфинал, Финал)
    # -------------------------------------------------------------------------
    teams_8 = [
        "Команда A", "Команда B", "Команда C", "Команда D",
        "Команда E", "Команда F", "Команда G", "Команда H"
    ]
    tournament_name_8 = "Чемпионат по программированию (8 команд)"
    round_names_8 = ["1/4 финала", "Полуфинал", "Финал"]

    draw_tournament_bracket(teams_8, tournament_name_8, round_names_8)
    logger.info("\n" + "="*60 + "\n")

    # -------------------------------------------------------------------------
    # Пример 3: 3 команды (минимальный случай с BYE)
    # 3 команды -> 4 слота -> 2 раунда (Полуфинал, Финал)
    # -------------------------------------------------------------------------
    teams_3 = ["Team 1", "Team 2", "Team 3"]
    tournament_name_3 = "Mini Tournament"
    round_names_3 = ["Полуфинал", "Финал"]

    draw_tournament_bracket(teams_8, tournament_name_8, round_names_8)
    logger.info("\n" + "="*60 + "\n")

    # -------------------------------------------------------------------------
    # Пример 4: 16 команд (большая сетка)
    # -------------------------------------------------------------------------
    # teams_16 = [f"Team {i+1}" for i in range(16)]
    # round_names_16 = ["1/8 финала", "1/4 финала", "Полуфинал", "Финал"]
    # draw_tournament_bracket(teams_16, "Grand Championship", round_names_16)


# =============================================================================
# ОТРИСОВКА СЕТКИ ИЗ БАЗЫ ДАННЫХ
# =============================================================================

def draw_bracket_from_db(matches: list, tournament_name: str, output_path: str = "tournament_bracket_db.png"):
    """
    Рисует турнирную сетку напрямую из данных БД.

    Args:
        matches: Список матчей из get_bracket_matches()
        tournament_name: Название турнира
        output_path: Путь для сохранения
    """
    # Группируем матчи по раундам
    matches_by_round = {}
    for match in matches:
        round_num = match['round_number']
        if round_num not in matches_by_round:
            matches_by_round[round_num] = []
        matches_by_round[round_num].append(match)

    # Сортируем матчи в каждом раунде
    for round_num in matches_by_round:
        matches_by_round[round_num].sort(key=lambda m: m['match_number'])

    num_rounds = max(matches_by_round.keys())

    # Расчёт размеров
    first_round_count = len(matches_by_round.get(1, []))
    pair_height = 2 * BOX_H + PAIR_GAP
    canvas_height = START_Y * 2 + first_round_count * \
        pair_height + STAGE_GAP * (first_round_count - 1)
    canvas_width = START_X + STAGE_OFFSET_X * num_rounds + BOX_W + 100

    # Создаём изображение
    image = create_gradient_background(
        canvas_width, canvas_height, BACKGROUND_TOP, BACKGROUND_BOTTOM)
    draw = ImageDraw.Draw(image)

    # Шрифты
    font_team = get_font(FONT_SIZE_TEAM)
    font_round = get_font(FONT_SIZE_ROUND, bold=True)
    font_title = get_font(FONT_SIZE_TITLE, bold=True)

    # Заголовок
    title_bbox = draw.textbbox((0, 0), tournament_name, font=font_title)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (canvas_width - title_width) // 2
    draw.text((title_x, 40), tournament_name,
              fill=TITLE_COLOR, font=font_title)

    # Базовая Y-координата
    base_start_y = START_Y

    # Храним координаты для линий
    # block_coords[round_num][match_num] = (x_right, y_top_center, y_bottom_center)
    block_coords = {}

    for round_num in range(1, num_rounds + 1):
        round_matches = matches_by_round.get(round_num, [])
        if not round_matches:
            continue

        round_name = round_matches[0]['round_name'] if round_matches and round_matches[0]['round_name'] else get_round_name_db(round_num, num_rounds)
        round_x = START_X + (round_num - 1) * STAGE_OFFSET_X

        # Название раунда
        name_bbox = draw.textbbox((0, 0), round_name, font=font_round)
        name_width = name_bbox[2] - name_bbox[0]
        name_x = round_x + (BOX_W - name_width) // 2
        draw.text((name_x, base_start_y - TITLE_OFFSET_Y - FONT_SIZE_ROUND),
                  round_name, fill=ROUND_TEXT_COLOR, font=font_round)

        block_coords[round_num] = {}

        for match in round_matches:
            # Вычисляем Y-позицию
            y_top, y_bottom = calculate_match_y_db(
                round_num, match['match_number'], base_start_y,
                pair_height, STAGE_GAP, first_round_count
            )

            # Названия команд. Для BYE-матча пустой слот показываем как BYE.
            is_bye_match = bool(match['is_bye'])
            team1_name = match['team1_name'] if match['team1_name'] else (
                "BYE" if is_bye_match and not match['team1_id'] else "???"
            )
            team2_name = match['team2_name'] if match['team2_name'] else (
                "BYE" if is_bye_match and not match['team2_id'] else "???"
            )

            # Добавляем значок победителя
            if match['winner_id']:
                if match['winner_id'] == match['team1_id']:
                    team1_name = f"🏆 {team1_name}"
                elif match['winner_id'] == match['team2_id']:
                    team2_name = f"🏆 {team2_name}"

            # Рисуем блоки
            draw_rounded_rectangle(draw, round_x, y_top,
                                   BOX_W, BOX_H, RADIUS, BOX_COLOR)
            draw_rounded_rectangle(
                draw, round_x, y_bottom, BOX_W, BOX_H, RADIUS, BOX_COLOR)

            # Текст команд
            team1_bbox = draw.textbbox((0, 0), team1_name, font=font_team)
            team1_width = team1_bbox[2] - team1_bbox[0]
            team1_x = round_x + (BOX_W - team1_width) // 2

            team2_bbox = draw.textbbox((0, 0), team2_name, font=font_team)
            team2_width = team2_bbox[2] - team2_bbox[0]
            team2_x = round_x + (BOX_W - team2_width) // 2

            draw.text((team1_x, y_top + 20), team1_name,
                      fill=TEXT_COLOR, font=font_team)
            draw.text((team2_x, y_bottom + 20), team2_name,
                      fill=TEXT_COLOR, font=font_team)

            # Сохраняем координаты
            y_center_top = y_top + BOX_H // 2
            y_center_bottom = y_bottom + BOX_H // 2
            block_coords[round_num][match['match_number']] = (
                round_x + BOX_W, y_center_top, y_center_bottom)

    # Рисуем соединительные линии
    for round_num in range(1, num_rounds):
        if round_num not in block_coords:
            continue

        next_round_num = round_num + 1
        if next_round_num not in block_coords:
            continue

        for match_num, (start_x, y_top, y_bottom) in block_coords[round_num].items():
            next_match_num = (match_num + 1) // 2
            if next_match_num not in block_coords[next_round_num]:
                continue

            next_x, _, next_y_center = block_coords[next_round_num][next_match_num]
            mid_x = next_x - LINE_MID_OFFSET

            # Рисуем линии
            draw.line([(start_x, y_top), (mid_x, y_top)],
                      fill=LINE_COLOR, width=LINE_WIDTH)
            draw.line([(start_x, y_bottom), (mid_x, y_bottom)],
                      fill=LINE_COLOR, width=LINE_WIDTH)
            draw.line([(mid_x, y_top), (mid_x, y_bottom)],
                      fill=LINE_COLOR, width=LINE_WIDTH)
            draw.line([(mid_x, next_y_center), (next_x, next_y_center)],
                      fill=LINE_COLOR, width=LINE_WIDTH)

    # Сохраняем
    image.save(output_path, 'PNG')
    logger.info(f"  [DB Bracket] ✅ Сохранено: {output_path}")
    return output_path


def get_round_name_db(round_number: int, total_rounds: int) -> str:
    """Возвращает название раунда для сетки из БД."""
    if total_rounds == 1 and round_number == 1:
        return "Финал"
    elif total_rounds == 2:
        return "Полуфинал" if round_number == 1 else "Финал"
    elif total_rounds == 3:
        names = {1: "1/4 финала", 2: "Полуфинал", 3: "Финал"}
        return names.get(round_number, f"Раунд {round_number}")
    elif total_rounds == 4:
        names = {1: "1/8 финала", 2: "1/4 финала", 3: "Полуфинал", 4: "Финал"}
        return names.get(round_number, f"Раунд {round_number}")
    else:
        return f"Раунд {round_number}"


def calculate_match_y_db(round_num: int, match_num: int, base_start_y: int,
                         pair_height: int, stage_gap: int, first_round_count: int) -> tuple:
    """Вычисляет Y-координаты для матча в сетке из БД."""
    if round_num == 1:
        y_offset = (match_num - 1) * (pair_height + stage_gap)
        y_top = base_start_y + y_offset
        y_bottom = y_top + BOX_H + PAIR_GAP
    else:
        # Для следующих раундов: позиция посередине между соответствующими парами раунда 1
        pairs_per_match = 2 ** (round_num - 1)
        first_pair_idx = (match_num - 1) * pairs_per_match
        last_pair_idx = first_pair_idx + pairs_per_match - 1

        # Учитываем количество пар в первом раунде
        if first_pair_idx >= first_round_count:
            # За пределами первого раунда
            y_top = base_start_y
            y_bottom = y_top + BOX_H + PAIR_GAP
        else:
            y_top_first = base_start_y + \
                first_pair_idx * (pair_height + stage_gap)
            y_top_last = base_start_y + \
                last_pair_idx * (pair_height + stage_gap)

            group_center_y = (y_top_first + y_top_last) / \
                2 + BOX_H + PAIR_GAP / 2
            y_top = int(group_center_y - BOX_H / 2 - PAIR_GAP / 2)
            y_bottom = int(group_center_y + BOX_H / 2 + PAIR_GAP / 2)

    return y_top, y_bottom