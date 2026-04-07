#!/usr/bin/env python3
"""
Snake 4K — Famicom-paced snake at 60 FPS.
Main menu, NES-style colors, grid step timing tuned like a classic console title.
Zero-shot start: snake begins as one cell (head only); every apple adds one segment.
SFX: Famicom-style square-wave bleeps; external sound files off (synthesized in RAM).
"""

import io
import random
import struct
import sys
import wave

import pygame

# --- audio: disk files off; procedural WAV in memory only ---
SFX_FILES = False
SAMPLE_RATE = 22050

# --- display / timing (60 FPS render; Famicom-style step rate) ---
SW, SH = 720, 540
FPS = 60
# Grid moves every N frames ≈ NES home-computer snake feel (~5–6 steps/sec at 12)
MOVE_EVERY_FRAMES = 12

CELL = 18
GRID_W = (SW - 160) // CELL
GRID_H = (SH - 80) // CELL
ORIGIN_X = (SW - GRID_W * CELL) // 2
ORIGIN_Y = (SH - GRID_H * CELL) // 2 + 10

# NES-ish palette
C_BG = (16, 56, 16)
C_GRID = (32, 96, 32)
C_HEAD = (252, 252, 116)
C_BODY = (88, 184, 88)
C_FOOD = (236, 92, 92)
C_UI = (252, 252, 252)
C_DIM = (180, 200, 180)
C_MENU_HI = (255, 220, 80)
C_PANEL = (8, 24, 8)


class GameState:
    MENU = "menu"
    HOWTO = "howto"
    ABOUT = "about"
    CREDITS = "credits"
    PLAY = "play"
    GAMEOVER = "gameover"


DIR = {
    pygame.K_UP: (0, -1),
    pygame.K_DOWN: (0, 1),
    pygame.K_LEFT: (-1, 0),
    pygame.K_RIGHT: (1, 0),
    pygame.K_w: (0, -1),
    pygame.K_s: (0, 1),
    pygame.K_a: (-1, 0),
    pygame.K_d: (1, 0),
}


def draw_text_block(screen, font, lines, x, y, color, line_gap=4):
    for i, line in enumerate(lines):
        surf = font.render(line, True, color)
        screen.blit(surf, (x, y + i * (font.get_height() + line_gap)))


def _sound_from_samples(samples):
    """Build pygame Sound from mono int16 samples (no filesystem)."""
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    bio.seek(0)
    return pygame.mixer.Sound(file=bio)


def _square_tone(freq, ms, vol=0.22):
    """One square-wave burst (2A03-ish timbre at low sample rate)."""
    n = max(1, int(SAMPLE_RATE * ms / 1000))
    period = SAMPLE_RATE / max(20.0, float(freq))
    out = []
    for i in range(n):
        ph = (i % period) / period
        s = 1.0 if ph < 0.5 else -1.0
        edge = min(i, n - 1 - i, int(SAMPLE_RATE * 0.004))
        env = min(1.0, edge / max(1.0, SAMPLE_RATE * 0.004))
        out.append(int(s * vol * env * 32767))
    return out


def build_famicon_sfx():
    """Short NES/Famicom-style SFX (no disk files; see SFX_FILES)."""
    # menu cursor
    blip = _square_tone(320, 18, 0.18)
    # menu confirm
    ok = _square_tone(440, 35, 0.2) + _square_tone(660, 55, 0.2)
    # pickup: quick upward arpeggio
    pickup = []
    for f, ms in ((523, 40), (659, 40), (784, 65)):
        pickup.extend(_square_tone(f, ms, 0.2))
    # game over: downward slam + noise tail
    die = []
    for f, ms in ((392, 70), (262, 90), (165, 120)):
        die.extend(_square_tone(f, ms, 0.24))
    rng = random.Random(42)
    for i in range(int(SAMPLE_RATE * 0.12)):
        die.append(int((rng.random() * 2 - 1) * 0.12 * 32767))
    return {
        "blip": _sound_from_samples(blip),
        "ok": _sound_from_samples(ok),
        "pickup": _sound_from_samples(pickup),
        "die": _sound_from_samples(die),
    }


def main():
    pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 512)
    pygame.init()
    pygame.display.set_caption("AC Holdings's snake py port")
    screen = pygame.display.set_mode((SW, SH))
    clock = pygame.time.Clock()

    try:
        font = pygame.font.SysFont("Menlo", 22)
        font_big = pygame.font.SysFont("Menlo", 36, bold=True)
        font_small = pygame.font.SysFont("Menlo", 18)
    except Exception:
        font = pygame.font.Font(None, 26)
        font_big = pygame.font.Font(None, 40)
        font_small = pygame.font.Font(None, 22)

    sfx = None

    def sfx_play(name):
        if sfx and name in sfx:
            sfx[name].play()

    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=SAMPLE_RATE, channels=1)
        sfx = build_famicon_sfx()
    except (pygame.error, OSError, struct.error):
        sfx = None

    state = GameState.MENU
    menu_sel = 0
    menu_items = [
        ("Play Game", GameState.PLAY),
        ("How to Play", GameState.HOWTO),
        ("About", GameState.ABOUT),
        ("Credits", GameState.CREDITS),
        ("Exit Game", "quit"),
    ]

    snake = []
    direction = (1, 0)
    pending_dir = (1, 0)
    food = (0, 0)
    move_timer = 0
    score = 0
    game_over_msg = ""

    def reset_game():
        nonlocal snake, direction, pending_dir, food, move_timer, score, game_over_msg
        cx, cy = GRID_W // 2, GRID_H // 2
        # Zero-shot: single-cell snake (head only)
        snake = [(cx, cy)]
        direction = (1, 0)
        pending_dir = (1, 0)
        move_timer = 0
        score = 0
        game_over_msg = ""
        occupied = set(snake)

        def rand_food():
            while True:
                fx = random.randrange(GRID_W)
                fy = random.randrange(GRID_H)
                if (fx, fy) not in occupied:
                    return fx, fy

        food = rand_food()

    running = True
    while running:
        clock.tick(FPS)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if state == GameState.MENU:
                    if event.key == pygame.K_UP:
                        menu_sel = (menu_sel - 1) % len(menu_items)
                        sfx_play("blip")
                    elif event.key == pygame.K_DOWN:
                        menu_sel = (menu_sel + 1) % len(menu_items)
                        sfx_play("blip")
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        choice = menu_items[menu_sel][1]
                        if choice == "quit":
                            running = False
                        elif choice == GameState.PLAY:
                            sfx_play("ok")
                            reset_game()
                            state = GameState.PLAY
                        else:
                            sfx_play("ok")
                            state = choice
                    elif event.key == pygame.K_ESCAPE:
                        running = False

                elif state in (GameState.HOWTO, GameState.ABOUT, GameState.CREDITS):
                    if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE):
                        sfx_play("blip")
                        state = GameState.MENU

                elif state == GameState.PLAY:
                    if event.key == pygame.K_ESCAPE:
                        sfx_play("blip")
                        state = GameState.MENU
                    elif event.key in DIR:
                        nx, ny = DIR[event.key]
                        px, py = pending_dir
                        # no instant 180
                        if (nx, ny) != (-px, -py):
                            pending_dir = (nx, ny)

                elif state == GameState.GAMEOVER:
                    if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        sfx_play("ok")
                        reset_game()
                        state = GameState.PLAY
                    elif event.key == pygame.K_ESCAPE:
                        sfx_play("blip")
                        state = GameState.MENU

        # --- simulate snake on Famicom-style step clock ---
        if state == GameState.PLAY:
            move_timer += 1
            if move_timer >= MOVE_EVERY_FRAMES:
                move_timer = 0
                direction = pending_dir
                hx, hy = snake[0]
                dx, dy = direction
                nx, ny = hx + dx, hy + dy

                if nx < 0 or nx >= GRID_W or ny < 0 or ny >= GRID_H:
                    game_over_msg = "Wall!"
                    state = GameState.GAMEOVER
                    sfx_play("die")
                elif (nx, ny) in snake:
                    game_over_msg = "Bit yourself!"
                    state = GameState.GAMEOVER
                    sfx_play("die")
                else:
                    snake.insert(0, (nx, ny))
                    if (nx, ny) == food:
                        score += 1
                        sfx_play("pickup")
                        pool = set(snake)
                        while True:
                            fx = random.randrange(GRID_W)
                            fy = random.randrange(GRID_H)
                            if (fx, fy) not in pool:
                                food = (fx, fy)
                                break
                    else:
                        snake.pop()

        # --- draw ---
        screen.fill(C_BG)

        if state == GameState.MENU:
            title = font_big.render("AC Holdings's snake py port", True, C_MENU_HI)
            screen.blit(title, title.get_rect(center=(SW // 2, 72)))
            sub = font_small.render("Famicom pace · 60 FPS · zero-shot start · SFX files off", True, C_DIM)
            screen.blit(sub, sub.get_rect(center=(SW // 2, 108)))
            copy_atari = font_small.render("[C] Atari 1972", True, C_DIM)
            screen.blit(copy_atari, copy_atari.get_rect(center=(SW // 2, 132)))
            copy_ac = font_small.render("[C] AC Holdings 1999-2026", True, C_DIM)
            screen.blit(copy_ac, copy_ac.get_rect(center=(SW // 2, 154)))

            y0 = 210
            for i, (label, _) in enumerate(menu_items):
                c = C_MENU_HI if i == menu_sel else C_UI
                t = font.render(("› " if i == menu_sel else "  ") + label, True, c)
                screen.blit(t, (SW // 2 - t.get_width() // 2, y0 + i * 42))
            hint = font_small.render("↑↓ choose · Enter · Esc quit", True, C_DIM)
            screen.blit(hint, hint.get_rect(center=(SW // 2, SH - 48)))

        elif state == GameState.HOWTO:
            lines = [
                "Move with arrow keys or WASD.",
                "Eat red apples to grow and score.",
                "You start as one cell (zero-shot); each apple adds one segment.",
                "Walls and your own tail are instant game over.",
                "Snake moves on a fixed timer like a Famicom cart —",
                "the window still refreshes at 60 FPS for smooth input.",
            ]
            draw_text_block(screen, font, lines, 60, 80, C_UI)
            foot = font_small.render("Esc / Enter — back to menu", True, C_DIM)
            screen.blit(foot, (60, SH - 60))

        elif state == GameState.ABOUT:
            lines = [
                "Snake 4K is a small pygame snake with a main menu,",
                "NES-inspired colors, and step timing tuned for a",
                "classic console feel while rendering at 60 FPS.",
            ]
            draw_text_block(screen, font, lines, 60, 100, C_UI)
            foot = font_small.render("Esc / Enter — back to menu", True, C_DIM)
            screen.blit(foot, (60, SH - 60))

        elif state == GameState.CREDITS:
            lines = [
                "AC Holdings's snake py port",
                "[C] AC Holdings 1999-2026",
                "[C] Atari 1972",
                "Python + pygame · procedural Famicom-style SFX (no disk files)",
                "Thanks for playing.",
            ]
            draw_text_block(screen, font, lines, 60, 100, C_UI)
            foot = font_small.render("Esc / Enter — back to menu", True, C_DIM)
            screen.blit(foot, (60, SH - 60))

        elif state in (GameState.PLAY, GameState.GAMEOVER):
            # board frame
            rect = pygame.Rect(
                ORIGIN_X - 4,
                ORIGIN_Y - 4,
                GRID_W * CELL + 8,
                GRID_H * CELL + 8,
            )
            pygame.draw.rect(screen, C_GRID, rect, 2)

            for x in range(GRID_W + 1):
                gx = ORIGIN_X + x * CELL
                pygame.draw.line(screen, C_GRID, (gx, ORIGIN_Y), (gx, ORIGIN_Y + GRID_H * CELL), 1)
            for y in range(GRID_H + 1):
                gy = ORIGIN_Y + y * CELL
                pygame.draw.line(screen, C_GRID, (ORIGIN_X, gy), (ORIGIN_X + GRID_W * CELL, gy), 1)

            fx, fy = food
            fr = pygame.Rect(ORIGIN_X + fx * CELL + 2, ORIGIN_Y + fy * CELL + 2, CELL - 4, CELL - 4)
            pygame.draw.rect(screen, C_FOOD, fr)

            for i, (sx, sy) in enumerate(snake):
                r = pygame.Rect(ORIGIN_X + sx * CELL + 1, ORIGIN_Y + sy * CELL + 1, CELL - 2, CELL - 2)
                pygame.draw.rect(screen, C_HEAD if i == 0 else C_BODY, r)

            score_s = font.render(f"Score {score}", True, C_UI)
            screen.blit(score_s, (24, 18))
            pause_h = font_small.render("Esc — menu", True, C_DIM)
            screen.blit(pause_h, (24, 44))

            if state == GameState.GAMEOVER:
                overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 160))
                screen.blit(overlay, (0, 0))
                go = font_big.render("GAME OVER", True, C_FOOD)
                screen.blit(go, go.get_rect(center=(SW // 2, SH // 2 - 30)))
                reason = font.render(game_over_msg, True, C_UI)
                screen.blit(reason, reason.get_rect(center=(SW // 2, SH // 2 + 10)))
                again = font_small.render("Enter — play again   Esc — menu", True, C_DIM)
                screen.blit(again, again.get_rect(center=(SW // 2, SH // 2 + 52)))

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
