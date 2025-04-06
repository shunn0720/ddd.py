import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio

# MBTI相性表（各MBTIタイプに対する上位3位）
compatibility = {
    "建築家": ["領事", "討論者", "冒険家"],
    "論理学者": ["エンターテイナー", "指揮官", "擁護者"],
    "指揮官": ["擁護者", "論理学者", "エンターテイナー"],
    "討論者": ["冒険家", "建築家", "領事"],
    "提唱者": ["幹部", "運動家", "巨匠"],
    "仲介者": ["起業家", "主人公", "管理者"],
    "主人公": ["管理者", "仲介者", "起業家"],
    "運動家": ["巨匠", "提唱者", "幹部"],
    "管理者": ["主人公", "仲介者", "起業家"],
    "擁護者": ["指揮官", "論理学者", "エンターテイナー"],
    "幹部": ["提唱者", "運動家", "巨匠"],
    "領事": ["建築家", "冒険家", "討論者"],
    "巨匠": ["運動家", "幹部", "提唱者"],
    "起業家": ["仲介者", "管理者", "主人公"],
    "エンターテイナー": ["論理学者", "指揮官", "擁護者"],
    "冒険家": ["討論者", "運動家", "主人公"]
}

# 各MBTIロールに対応するDiscordのロールID（サーバーにあらかじめ設定済み）
role_ids = {
    "建築家": "1304800473747820638",
    "論理学者": "1304800591951695932",
    "討論者": "1304807122919231548",
    "指揮官": "1304800682779611228",
    "提唱者": "1304800727897735219",
    "仲介者": "1304800858877464628",
    "主人公": "1304800907829182514",
    "運動家": "1304800946743803914",
    "管理者": "1304801038661976105",
    "擁護者": "1304801090981855302",
    "幹部": "1304801178357334097",
    "領事": "1304801223681118229",
    "巨匠": "1304801231868264519",
    "起業家": "1304801447493505055",
    "エンターテイナー": "1304801505303330897",
    "冒険家": "1304801393386983505"
}

# 対象の2チャンネルID（テキストチャンネルとして投稿履歴を検索）
TARGET_CHANNEL_IDS = [1304813185920139306, 1304813222058004522]

class MBTICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_user_mbti(self, guild: discord.Guild, user: discord.User) -> str:
        """ユーザーのMBTIロールを、ギルド上のロールまたは対象チャンネルの投稿から取得する"""
        # ① ギルド上のロールから取得
        for role in user.roles:
            if str(role.id) in role_ids.values():
                for mbti, r_id in role_ids.items():
                    if str(role.id) == r_id:
                        return mbti
        # ② 対象チャンネルの投稿履歴を走査して、ユーザーの発言時のロール情報から取得
        for cid in TARGET_CHANNEL_IDS:
            channel = guild.get_channel(cid)
            if channel is None:
                continue
            try:
                async for message in channel.history(limit=100):
                    if message.author.id == user.id:
                        for role in message.author.roles:
                            if str(role.id) in role_ids.values():
                                for mbti, r_id in role_ids.items():
                                    if str(role.id) == r_id:
                                        return mbti
            except Exception as e:
                print(f"チャンネル {cid} の履歴取得エラー: {e}")
        return None

    @app_commands.command(name="相性診断", description="あなたのMBTIに基づく相性上位3のユーザー投稿をDMに送信します。")
    async def compatibility(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # コマンド発行者のMBTIロール取得（ギルド上＋対象チャンネルの投稿から）
        user_mbti = await self.get_user_mbti(guild, user)

        if user_mbti is None:
            try:
                await user.send("あなたのMBTIロールが見つかりませんでした。")
            except Exception:
                pass
            await interaction.response.send_message("MBTIロールが見つかりませんでした。DM設定をご確認ください。", ephemeral=True)
            return

        if user_mbti not in compatibility:
            try:
                await user.send("あなたのMBTIロールに対応する相性情報が見つかりませんでした。")
            except Exception:
                pass
            await interaction.response.send_message("相性情報が見つかりませんでした。", ephemeral=True)
            return

        best_matches = compatibility[user_mbti]

        # 各相性MBTIごとに、ユーザーごとの最新投稿を記録する辞書を作成
        mbti_messages = { mbti: {} for mbti in best_matches }

        for cid in TARGET_CHANNEL_IDS:
            channel = guild.get_channel(cid)
            if channel is None:
                continue
            try:
                # 過去100件の投稿履歴を取得（必要に応じて調整）
                async for message in channel.history(limit=100):
                    # 各相性MBTIロールごとにチェック
                    for mbti in best_matches:
                        role_id = role_ids.get(mbti)
                        if role_id is None:
                            continue
                        if any(str(r.id) == role_id for r in message.author.roles):
                            # 同一ユーザーの場合、最新の投稿を保存
                            if (message.author.id not in mbti_messages[mbti] or
                                message.created_at > mbti_messages[mbti][message.author.id].created_at):
                                mbti_messages[mbti][message.author.id] = message
            except Exception as e:
                print(f"チャンネル {cid} の履歴取得エラー: {e}")

        # 結果のEmbed作成
        embed = discord.Embed(
            title="MBTI相性診断結果",
            description=f"あなたのMBTIロールは **{user_mbti}** です。\n以下は相性上位3のMBTIロールのユーザー投稿です。\n※ユーザー名をクリックすると該当投稿にジャンプします。",
            color=0x00AAFF
        )
        for idx, mbti in enumerate(best_matches, start=1):
            messages = mbti_messages.get(mbti, {})
            if messages:
                links = []
                for uid, message in messages.items():
                    # Discord投稿リンクの形式: https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
                    link = f"https://discord.com/channels/{guild.id}/{message.channel.id}/{message.id}"
                    links.append(f"[<@{uid}>]({link})")
                field_value = ", ".join(links)
            else:
                field_value = "該当投稿なし"
            embed.add_field(name=f"{idx}位 {mbti}", value=field_value, inline=False)

        # DM送信
        try:
            await user.send(embed=embed)
        except Exception:
            await interaction.response.send_message("DMの送信に失敗しました。DM設定をご確認ください。", ephemeral=True)
            return

        # コマンド入力されたテキストチャンネルに通知
        await interaction.response.send_message(f"<@{user.id}> さん、DMに結果を送ったから見てね。")

# Botの起動設定
intents = discord.Intents.default()
intents.message_content = True  # 必要に応じて
# MBTI判定にメンバー情報が必要なら intents.members = True を有効にし、
# もしくは Developer Portal で Privileged Intent を有効化してください。
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"スラッシュコマンド同期エラー: {e}")

# Cogの追加とBotの起動
async def setup():
    await bot.add_cog(MBTICog(bot))

async def main():
    async with bot:
        await setup()
        await bot.start(os.getenv("BOT_TOKEN"))

if __name__ == "__main__":
    token = os.getenv("BOT_TOKEN")
    if token is None:
        print("Error: BOT_TOKEN が環境変数に設定されていません。")
        exit(1)
    asyncio.run(main())
