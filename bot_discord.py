import discord
import os
import json
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))

# Intents padrão do bot
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.id != CHANNEL_ID:
        return  # ignora se não for no canal autorizado

# Arquivo com os dados do bot de trading
arquivo_dados = "dados_bot.json"

# Função para carregar os dados salvos
def carregar_dados():
    with open(arquivo_dados, "r") as f:
        return json.load(f)

# Evento de conexão
@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

# Evento de mensagem

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Comando: !saldo
    if message.content.lower() == "!saldo":
        dados = carregar_dados()
        saldo_atual = dados["historico_patrimonio"][-1]["saldo_total_usdt"] if dados["historico_patrimonio"] else 0
        historico = dados["historico_patrimonio"]

        def buscar_antigo(horas):
            from datetime import datetime, timedelta
            alvo = datetime.now() - timedelta(hours=horas)
            for item in reversed(historico):
                t = datetime.strptime(item["timestamp"], "%Y-%m-%d %H:%M:%S")
                if t <= alvo:
                    return item["saldo_total_usdt"]
            return None

        antigo_24h = buscar_antigo(24)
        antigo_7d = buscar_antigo(24*7)

        variacao_24h = f"{((saldo_atual - antigo_24h) / antigo_24h) * 100:.2f}%" if antigo_24h else "N/A"
        variacao_7d = f"{((saldo_atual - antigo_7d) / antigo_7d) * 100:.2f}%" if antigo_7d else "N/A"

        resposta = (
            f"📊 **Resumo Atual**:\n"
            f"💰 Saldo Total: **{saldo_atual:.2f} USDT**\n"
            f"📈 Valorização em 24h: {variacao_24h}\n"
            f"📈 Valorização em 7 dias: {variacao_7d}"
        )
        await message.channel.send(resposta)

    # Comando: !grafico BTCUSDT ou !grafico SOLUSDT
    elif message.content.lower().startswith("!grafico"):
        partes = message.content.split()
        if len(partes) == 2:
            symbol = partes[1].upper()
            arquivo = f"grafico_{symbol}.png"
            if os.path.exists(arquivo):
                await message.channel.send(f"📊 Gráfico mais recente de {symbol}:", file=discord.File(arquivo))
            else:
                await message.channel.send(f"❌ Nenhum gráfico encontrado para {symbol}.")

# Inicia o bot
client.run(DISCORD_TOKEN)
