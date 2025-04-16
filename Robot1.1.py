import pandas as pd
import os
import time
from binance.client import Client
from binance.enums import *
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from binance.exceptions import BinanceAPIException
import json
import matplotlib.pyplot as plt

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

api_key = os.getenv("KEY_BINANCE")
secret_key = os.getenv("SECRET_BINANCE")

cliente_binance = Client(api_key, secret_key)

moedas = ["BTCUSDT", "SOLUSDT"]
periodo_candle = Client.KLINE_INTERVAL_1HOUR
percentual_stop_loss = 0.03
percentual_take_profit = 0.04
intervalo_verificacao = 60 * 60
arquivo_dados = "dados_bot.json"

# Cria pastas necessárias
os.makedirs("graficos", exist_ok=True)

def salvar_dados(dados):
    with open(arquivo_dados, "w") as f:
        json.dump(dados, f, indent=4)

def carregar_dados():
    dados_padrao = {
        "posicoes": {moeda: False for moeda in moedas},
        "precos_compra": {moeda: 0 for moeda in moedas},
        "stop_losses": {moeda: 0 for moeda in moedas},
        "take_profits": {moeda: 0 for moeda in moedas},
        "historico_patrimonio": []
    }
    try:
        with open(arquivo_dados, "r") as f:
            dados = json.load(f)
            for chave, valor in dados_padrao.items():
                if chave not in dados:
                    dados[chave] = valor
            return dados
    except (FileNotFoundError, json.JSONDecodeError):
        return dados_padrao

def pegar_saldo():
    saldo = {"USDT": 0, "BTC": 0, "SOL": 0}
    try:
        conta = cliente_binance.get_account()
        for ativo in conta['balances']:
            if ativo['asset'] in saldo:
                saldo[ativo['asset']] = float(ativo['free'])
    except:
        pass
    return saldo

def pegar_precos():
    precos = {}
    for moeda in ["BTCUSDT", "SOLUSDT"]:
        try:
            precos[moeda] = float(cliente_binance.get_symbol_ticker(symbol=moeda)['price'])
        except:
            precos[moeda] = 0
    return precos

def atualizar_historico(dados):
    saldo = pegar_saldo()
    precos = pegar_precos()
    total_usdt = saldo["USDT"] + saldo["BTC"] * precos["BTCUSDT"] + saldo["SOL"] * precos["SOLUSDT"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dados.setdefault("historico_patrimonio", []).append({"timestamp": timestamp, "saldo_total_usdt": total_usdt})
    dados["historico_patrimonio"] = dados["historico_patrimonio"][-168:]
    return dados

def mostrar_valorizacao(dados):
    historico = dados.get("historico_patrimonio", [])
    if len(historico) < 2:
        return
    atual = historico[-1]["saldo_total_usdt"]
    def buscar_antigo(horas):
        alvo = datetime.now() - timedelta(hours=horas)
        for item in reversed(historico):
            t = datetime.strptime(item["timestamp"], "%Y-%m-%d %H:%M:%S")
            if t <= alvo:
                return item["saldo_total_usdt"]
        return None
    antigo_24h = buscar_antigo(24)
    antigo_7d = buscar_antigo(24 * 7)
    if antigo_24h:
        variacao = ((atual - antigo_24h) / antigo_24h) * 100
        logging.info(f"📈 Valorização em 24h: {variacao:.2f}%")
    if antigo_7d:
        variacao = ((atual - antigo_7d) / antigo_7d) * 100
        logging.info(f"📊 Valorização em 7 dias: {variacao:.2f}%")

def pegar_dados(codigo):
    candles = cliente_binance.get_klines(symbol=codigo, interval=periodo_candle, limit=100)
    df = pd.DataFrame(candles)
    df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                  "quote_asset_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = df[["close", "close_time"]]
    df["close"] = df["close"].astype(float)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df

def calcular_medias(df):
    df["media_curta"] = df["close"].rolling(window=7).mean()
    df["media_longa"] = df["close"].rolling(window=40).mean()
    return df

def obter_lot_size(symbol):
    info = cliente_binance.get_symbol_info(symbol)
    lot = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
    notional = next(f for f in info['filters'] if f['filterType'] in ['MIN_NOTIONAL', 'NOTIONAL'])
    return float(lot['minQty']), float(lot['stepSize']), float(notional['minNotional'])

def ajustar_quantidade(symbol, quantidade, saldo_disponivel, preco):
    min_qty, step, min_notional = obter_lot_size(symbol)
    if min_qty == 0:
        return "0"
    quantidade = float(quantidade)
    quantidade = max(min_qty, round(quantidade // step * step, 8))
    quantidade = min(quantidade, saldo_disponivel)
    if quantidade * preco < min_notional:
        return "0"
    return f"{quantidade:.8f}".rstrip('0').rstrip('.')

def mostrar_grafico(df, symbol):
    import os

    if not os.path.exists("graficos"):
        os.makedirs("graficos")


    # Cria pasta se não existir
    if not os.path.exists("graficos"):
        os.makedirs("graficos")

    plt.figure(figsize=(12, 6))
    plt.plot(df['close_time'], df['close'], label='Preço')
    plt.plot(df['close_time'], df['media_curta'], label='Média 7', linestyle='--')
    plt.plot(df['close_time'], df['media_longa'], label='Média 40', linestyle='--')
    plt.title(f'{symbol} - Gráfico')
    plt.xlabel('Tempo')
    plt.ylabel('Preço')
    plt.legend()
    plt.grid()
    print(f"Salvando gráfico para {symbol} em: graficos/grafico_{symbol}.png")

    nome_arquivo = os.path.join("graficos", f'grafico_{symbol}.png')
    plt.savefig(nome_arquivo)
    logging.info(f"✅ Gráfico salvo como {os.path.abspath(nome_arquivo)}")
    plt.close()

def executar_estrategia_balanceada(dados, saldo_usdt):
    metade_saldo = saldo_usdt / 2
    compras_realizadas = 0

    for moeda in moedas:
        df = pegar_dados(moeda)
        df = calcular_medias(df)
        preco_atual = float(cliente_binance.get_symbol_ticker(symbol=moeda)['price'])

        logging.info(f"{moeda} - Média 7: {df['media_curta'].iloc[-1]:.2f} | Média 40: {df['media_longa'].iloc[-1]:.2f}")
        mostrar_grafico(df, moeda)

        cruzou_para_cima = df["media_curta"].iloc[-2] <= df["media_longa"].iloc[-2] and df["media_curta"].iloc[-1] > df["media_longa"].iloc[-1]

        if cruzou_para_cima:
            logging.info(f"Sinal de compra detectado para {moeda}!")
            quantidade = metade_saldo / preco_atual
            quantidade = ajustar_quantidade(moeda, quantidade, metade_saldo, preco_atual)

            if float(quantidade) > 0:
                cliente_binance.create_order(
                    symbol=moeda,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_MARKET,
                    quantity=quantidade
                )
                logging.info(f"✅ Compra de {quantidade} {moeda} executada a {preco_atual:.2f} USDT")
                compras_realizadas += 1
            else:
                logging.info(f"Valor de {metade_saldo:.2f} USDT abaixo do mínimo para {moeda}")

    if compras_realizadas == 0:
        logging.info("Nenhum sinal de compra válido detectado neste ciclo.")
    else:
        logging.info(f"{compras_realizadas} compra(s) realizada(s) com saldo balanceado.")
    
while True:
    dados_salvos = carregar_dados()
    saldo = pegar_saldo()
    precos = pegar_precos()

    total_usdt = saldo["USDT"] + saldo["BTC"] * precos["BTCUSDT"] + saldo["SOL"] * precos["SOLUSDT"]

    logging.info("Resumo do saldo:")
    logging.info(f"USDT: {saldo['USDT']:.2f}")
    logging.info(f"BTC: {saldo['BTC']} (≈ {saldo['BTC'] * precos['BTCUSDT']:.2f} USDT)")
    logging.info(f"SOL: {saldo['SOL']} (≈ {saldo['SOL'] * precos['SOLUSDT']:.2f} USDT)")
    logging.info(f"Total estimado em USDT: {total_usdt:.2f}")

    dados_salvos = atualizar_historico(dados_salvos)
    mostrar_valorizacao(dados_salvos)
    salvar_dados(dados_salvos)

    # 🔥 Gera gráficos a cada ciclo
    for moeda in moedas:
        df = pegar_dados(moeda)
        df = calcular_medias(df)
        logging.info(f"{moeda} - Média 7: {df['media_curta'].iloc[-1]:.2f} | Média 40: {df['media_longa'].iloc[-1]:.2f}")
        mostrar_grafico(df, moeda)

    if saldo['USDT'] > 20:
        executar_estrategia_balanceada(dados_salvos, saldo['USDT'])

    logging.info("Aguardando próxima verificação...")
    time.sleep(intervalo_verificacao)
