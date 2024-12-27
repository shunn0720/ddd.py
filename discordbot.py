import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool, Error
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
import json
from typing import Optional, Callable, Dict, Any, List

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244
SPECIFIC_USER_ID = 822460191118721034

LAST_CHOSEN_AUTHORS: Dict[int, int] = {}
CURRENT_PANEL_MESSAGE_ID: Optional[int] = None

# Botの初期化
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# データベース接続プールの初期化
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
    logging.info("データベース接続プールが初期化されました。")
except Error as e:
    logging.error(f"データベース接続プール初期化エラー: {e}")
    db_pool = None
    exit()

# データベース接続の取得
def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("データベース接続プールが初期化されていません。")
    except Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

# データベース接続の解放
def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

# データベースの初期化
def initialize_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE,
                thread_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                reactions JSONB DEFAULT '{}'::jsonb,
                content TEXT
            )
            """)
            conn.commit()
        logging.info("データベースの初期化が完了しました。")
    except Error as e:
        logging.error(f"テーブル初期化中エラー: {e}")
    finally:
        release_db_connection(conn)

initialize_db()

# スレッドプールで関数を実行する
async def run_in_threadpool(func: Callable, *args, **kwargs):
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, func, *args, **kwargs)
    except Exception as e:
        logging.error(f"スレッドプール実行中にエラー: {e}")
        return None

# メッセージをデータベースに保存する
def save_message_to_db_sync(message_id: int, author_id: int, content: str):
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_json = json.dumps({})
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE
            SET thread_id = EXCLUDED.thread_id,
                author_id = EXCLUDED.author_id,
                reactions = EXCLUDED.reactions,
                content = EXCLUDED.content
            """, (message_id, THREAD_ID, author_id, reactions_json, content))
            conn.commit()
        logging.debug(f"メッセージを保存しました: message_id={message_id}, author_id={author_id}")
    except Error as e:
        logging.error(f"メッセージ保存中エラー: {e}")
    finally:
        release_db_connection(conn)

async def save_message_to_db(message: discord.Message):
    await run_in_threadpool(save_message_to_db_sync, message.id, message.author.id, message.content)

# リアクションをデータベースに保存する
def update_reactions_in_db_sync(message_id: int, emoji_id: int, user_id: int, add: bool = True):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                logging.warning(f"メッセージID {message_id} がデータベースに存在しません。リアクションを無視します。")
                return
            reactions = row['reactions'] or {}
            if isinstance(reactions, str):
                try:
                    reactions = json.loads(reactions)
                except json.JSONDecodeError as e:
                    logging.error(f"JSONデコードエラー: {reactions}")
                    return
            str_emoji_id = str(emoji_id)
            user_list = reactions.get(str_emoji_id, [])

            if add:
                if user_id not in user_list:
                    user_list.append(user_id)
                    logging.debug(f"リアクション追加: message_id={message_id}, emoji_id={emoji_id}, user_id={user_id}")
            else:
                if user_id in user_list:
                    user_list.remove(user_id)
                    logging.debug(f"リアクション削除: message_id={message_id}, emoji_id={emoji_id}, user_id={user_id}")

            reactions[str_emoji_id] = user_list
            cur.execute("UPDATE messages SET reactions = %s WHERE message_id = %s", (json.dumps(reactions), message_id))
            conn.commit()
    except Error as e:
        logging.error(f"リアクション更新中エラー: {e}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id: int, emoji_id: int, user_id: int, add: bool = True):
    await run_in_threadpool(update_reactions_in_db_sync, message_id, emoji_id, user_id, add)

# リアクションを判定する
def user_reacted(msg: Dict[str, Any], reaction_id: int, user_id: int) -> bool:
    reaction_data = msg.get('reactions')
    if reaction_data is None:
        return False
    elif isinstance(reaction_data, str) and reaction_data:
        try:
            reaction_data = json.loads(reaction_data)
        except json.JSONDecodeError as e:
            logging.error(f"JSONデコードエラー: {reaction_data}")
            return False
    elif not isinstance(reaction_data, dict):
        return False
    users = reaction_data.get(reaction_id, [])
    return user_id in users

# ランダムなメッセージを取得する
def get_random_message_sync(thread_id: int, filter_func: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            query = "SELECT * FROM messages WHERE thread_id = %s"
            params = [thread_id]
            if filter_func:
                # ここでデータベースクエリで絞り込みを行う
                if filter_func.__name__ == "filter_func_read_later":
                    query += " AND reactions @> %s"
                    params.append(json.dumps({str(READ_LATER_REACTION_ID): []}))
                elif filter_func.__name__ == "filter_func_favorite":
                    query += " AND reactions @> %s"
                    params.append(json.dumps({str(FAVORITE_REACTION_ID): []}))
                elif filter_func.__name__ == "filter_func_random_exclude":
                    query += " AND NOT reactions @> %s"
                    params.append(json.dumps({str(RANDOM_EXCLUDE_REACTION_ID): []}))
                elif filter_func.__name__ == "filter_func_conditional_read":
                    query += " AND reactions @> %s AND NOT reactions @> %s"
                    params.append(json.dumps({str(READ_LATER_REACTION_ID): []}))
                    params.append(json.dumps({str(RANDOM_EXCLUDE_REACTION_ID): []}))
            cur.execute(query, params)
            messages = cur.fetchall()
            for m in messages:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions'])
                    except json.JSONDecodeError as e:
                        logging.error(f"JSONデコードエラー: {m['reactions']}")
                        m['reactions'] = {}
            if filter_func:
                messages = [m for m in messages if filter_func(m)]
            if not messages:
                return None
            return random.choice(messages)
    except Error as e:
        logging.error(f"ランダムメッセージ取得中エラー: {e}")
        return None
    finally:
        release_db_connection(conn)

async def get_random_message(thread_id: int, filter_func: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Optional[Dict[str, Any]]:
    return await run_in_threadpool(get_random_message_sync, thread_id, filter_func)

# メッセージを安全に取得する
async def safe_fetch_message(channel: discord.TextChannel, message_id: int, retry_count: int = 3) -> Optional[discord.Message]:
    for i in range(retry_count):
        try:
            message = await channel.fetch_message(message_id)
            return message
        except discord.NotFound:
            logging.warning(f"メッセージ {message_id} は存在しません。スキップします。")
            return None
        except discord.Forbidden:
            logging.error(f"メッセージ {message_id} へのアクセスが拒否されました。")
            return None
        except discord.HTTPException as e:
            logging.error(f"メッセージ {message_id} の取得中にHTTPエラーが発生しました: {e}, リトライ回数: {i+1}")
            await asyncio.sleep(1)
    logging.error(f"メッセージ {message_id} の取得に失敗しました。リトライ上限に達しました。")
    return None

# パネルを送信する
async def send_panel(channel: discord.TextChannel):
    global CURRENT_PANEL_MESSAGE_ID
    if CURRENT_PANEL_MESSAGE_ID:
        try:
            panel_message = await channel.fetch_message(CURRENT_PANEL_MESSAGE_ID)
            await panel_message.delete()
            logging.info(f"以前のパネルメッセージ {CURRENT_PANEL_MESSAGE_ID} を削除しました。")
        except discord.NotFound:
            logging.warning(f"以前のパネルメッセージ {CURRENT_PANEL_MESSAGE_ID} が見つかりません。")
        except (discord.HTTPException, discord.Forbidden) as e:
            logging.error(f"パネルメッセージ削除中エラー: {e}")
            return

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_message = await channel.send(embed=embed, view=view)
        CURRENT_PANEL_MESSAGE_ID = sent_message.id
        logging.info(f"新しいパネルメッセージ {CURRENT_PANEL_MESSAGE_ID} を送信しました。")
    except (discord.HTTPException, discord.Forbidden) as e:
        logging.error(f"パネルメッセージ送信中エラー: {e}")

# パネルのEmbedを作成する
def create_panel_embed():
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "ランダム：全体から選ぶ\n"
            "あとで読む：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "お気に入り：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=0xFF69B4
    )
    return embed

# 特定のユーザーかどうかを判定する
def is_specific_user():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == SPECIFIC_USER_ID
    return app_commands.check(predicate)

# ボタンのView
class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.bot_id = bot.user.id

    async def get_author_name(self, author_id: int) -> str:
      user = bot.get_user(author_id)
      if user is None:
          try:
              user = await bot.fetch_user(author_id)
          except discord.NotFound:
                logging.warning(f"ユーザーが見つかりませんでした。 author_id={author_id}")
                return "不明なユーザー"
          except Exception as e:
              logging.error(f"ユーザー取得中にエラーが発生しました。author_id={author_id}, {e}")
              return "不明なユーザー"
      return user.display_name if user and user.display_name else (user.name if user else "不明なユーザー")

    async def handle_selection(self, interaction: discord.Interaction, random_message: Optional[Dict[str, Any]]):
        try:
            if random_message:
                LAST_CHOSEN_AUTHORS[interaction.user.id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.channel.send(
                    f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.channel.send(
                    f"条件に合う投稿が見つかりませんでした。もう一度お試しください。"
                )
        except Exception as e:
            logging.error(f"メッセージ取得/応答中エラー: {e}")
            await interaction.channel.send(
                f"エラーが発生したため、また後で試してね。"
            )
        finally:
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction: discord.Interaction, filter_func: Callable[[Dict[str, Any]], bool]):
      try:
        await interaction.response.defer()
        random_message = await get_random_message(THREAD_ID, filter_func)
        if random_message:
            LAST_CHOSEN_AUTHORS[interaction.user.id] = random_message['author_id']
        await self.handle_selection(interaction, random_message)
      except Exception as e:
        logging.error(f"ボタン押下時エラー: {e}")
        await interaction.followup.send(f"処理中にエラーが発生しました。再試行してください。")

    def create_filter_function(self, interaction: discord.Interaction, reaction_id: Optional[int] = None, exclude_own: bool = True, exclude_reaction_id: Optional[int] = None) -> Callable[[Dict[str, Any]], bool]:
        def filter_func(msg: Dict[str, Any]) -> bool:
            if reaction_id is not None and not user_reacted(msg, reaction_id, interaction.user.id):
                logging.debug(f"  除外理由: 指定されたリアクションがない")
                return False
            if exclude_reaction_id is not None and user_reacted(msg, exclude_reaction_id, interaction.user.id):
                logging.debug(f"  除外理由: 指定された除外リアクションがある")
                return False
            if exclude_own and msg['author_id'] == interaction.user.id:
                logging.debug(f"  除外理由: 自分の投稿")
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                logging.debug(f"  除外理由: 特定の投稿者")
                return False
            if msg['author_id'] == self.bot_id:
                logging.debug(f"  除外理由: Botの投稿")
                return False
            if LAST_CHOSEN_AUTHORS.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"  除外理由: 前回選んだ投稿者")
                return False
            return True
        return filter_func

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="random_normal")
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        filter_func = self.create_filter_function(interaction)
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        filter_func = self.create_filter_function(interaction, reaction_id=READ_LATER_REACTION_ID)
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
       filter_func = self.create_filter_function(interaction, reaction_id=FAVORITE_REACTION_ID)
       await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="random_exclude")
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        filter_func = self.create_filter_function(interaction, exclude_reaction_id=RANDOM_EXCLUDE_REACTION_ID)
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read")
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        filter_func = self.create_filter_function(interaction, reaction_id=READ_LATER_REACTION_ID, exclude_reaction_id=RANDOM_EXCLUDE_REACTION_ID)
        await self.get_and_handle_random_message(interaction, filter_func)

# スラッシュコマンド
@bot.tree.command(name="panel")
@is_specific_user()
async def panel(interaction: discord.Interaction):
    channel = interaction.channel
    if channel is None:
        logging.error("コマンドを実行したチャンネルが取得できません。")
        await interaction.response.send_message("エラーが発生しました。チャンネルが特定できません。もう一度お試しください。", ephemeral=True)
        return

    await interaction.response.send_message("パネルを表示します！", ephemeral=True)
    await send_panel(channel)

@bot.tree.command(name="update_db")
@is_specific_user()
async def update_db(interaction: discord.Interaction):
    await interaction.response.send_message("データベースを更新しています...", ephemeral=True)
    try:
        await save_all_messages_to_db()
        await interaction.followup.send("全てのメッセージをデータベースに保存しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"update_dbコマンド中エラー: {e}")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再試行してください。", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
    else:
        logging.error(f"Unhandled app command error: {error}")
        await interaction.response.send_message("コマンド実行中にエラーが発生しました。もう一度お試しください。", ephemeral=True)

# メッセージイベント
@bot.event
async def on_message(message: discord.Message):
    if message.channel.id == THREAD_ID:
        await save_message_to_db(message)

# リアクションイベント
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    channel = None
    if payload.emoji.id is None:
        logging.warning("カスタム絵文字ではないリアクションが追加されました。")
        return

    emoji_name = payload.emoji.name
    emoji_id = payload.emoji.id
    logging.debug(f"カスタム絵文字リアクション追加: {emoji_name} (ID: {emoji_id})")

    if emoji_id == READ_LATER_REACTION_ID:
        logging.debug(f"特定の絵文字 <:b434:{READ_LATER_REACTION_ID}> がリアクションとして追加されました！")
    if emoji_id == FAVORITE_REACTION_ID:
        logging.debug(f"特定の絵文字 <:b435:{FAVORITE_REACTION_ID}> がリアクションとして追加されました！")
    if emoji_id == RANDOM_EXCLUDE_REACTION_ID:
        logging.debug(f"特定の絵文字 <:b431:{RANDOM_EXCLUDE_REACTION_ID}> がリアクションとして追加されました！")

    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id FROM messages WHERE message_id = %s", (payload.message_id,))
            if not cur.fetchone():
                channel = bot.get_channel(payload.channel_id)
                if channel:
                    message = await safe_fetch_message(channel, payload.message_id)
                    if message:
                        await save_message_to_db(message)
                        logging.debug(f"メッセージをデータベースに保存しました: message_id={payload.message_id}")
                    else:
                        logging.warning(f"メッセージ {payload.message_id} の取得に失敗しました。")
                else:
                    logging.error(f"チャンネル {payload.channel_id} が見つかりません。")
    except Error as e:
        logging.error(
            f"メッセージ存在確認中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)
    if channel:
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.emoji.id is None:
        logging.warning("カスタム絵文字ではないリアクションが削除されました。")
        return
    logging.debug(f"カスタム絵文字リアクション削除: {payload.emoji.name} (ID: {payload.emoji.id})")
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

# メッセージを定期的に保存するタスク
@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

def save_all_messages_to_db_sync(limit_count=100):
    conn = get_db_connection()
    if not conn:
        return
    release_db_connection(conn)

async def save_all_messages_to_db():
    channel = bot.get_channel(THREAD_ID)
    if channel:
        try:
            limit_count = 100
            messages = []
            async for message in channel.history(limit=limit_count):
                messages.append(message)
            if messages:
                await run_in_threadpool(bulk_save_messages_sync, messages)
            logging.info(f"最大{limit_count}件のメッセージをデータベースに保存しました。")
        except (discord.HTTPException, discord.Forbidden) as e:
            logging.error(f"メッセージ履歴取得中エラー: {e}")
    else:
        logging.error("指定されたTHREAD_IDのチャンネルが見つかりません。")

# Bot起動時の処理
@bot.event
async def on_ready():
    bot.add_view(CombinedView())
    await save_all_messages_to_db()
    logging.info(f"Botが起動しました！ {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンド同期中エラー: {e}")

# Botの起動
if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Bot起動中エラー: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("データベース接続プールをクローズしました。")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
