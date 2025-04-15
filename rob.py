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
import subprocess

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

api_key = os.getenv("KEY_BINANCE")
secret_key = os.getenv("SECRET_BINANCE")

cliente_binance = Client(api_key, secret_key)

# Configurações
moedas = ["BTCUSDT", "SOLUSDT"]
periodo_candle = Client.KLINE_INTERVAL_1HOUR
percentual_stop_loss = 0.03
percentual_take_profit = 0.04
intervalo_verificacao = 60 * 60
arquivo_dados = "dados_bot.json"

posicoes = {moeda: False for moeda in moedas}
precos_compra = {moeda: 0 for moeda in moedas}
stop_losses = {moeda: 0 for moeda in moedas}
take_profits = {moeda: 0 for moeda in moedas}

# Funções auxiliares
def salvar_dados(dados):
    with open(arquivo_dados, "w") as f:
        json.dump(dados, f, indent=4)

def carregar_dados():
    try:
        with open(arquivo_dados, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"posicoes": {}, "precos_compra": {}, "stop_losses": {}, "take_profits": {}, "historico_patrimonio": []}

def obter_lot_size(symbol):
    try:
        info = cliente_binance.get_symbol_info(symbol)
        lot = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
        notional = next(f for f in info['filters'] if f['filterType'] in ['MIN_NOTIONAL', 'NOTIONAL'])
        return float(lot['minQty']), float(lot['stepSize']), float(notional['minNotional'])
    except:
        return 0, 0, 0

def pegar_saldo():
    saldo = {"USDT": 0, "BTC": 0, "SOL": 0, "ETH": 0}
    try:
        conta = cliente_binance.get_account()
        for ativo in conta['balances']:
            if ativo['asset'] in saldo:
                saldo[ativo['asset']] = float(ativo['free'])
    except:
        pass
    return saldo

def atualizar_historico(dados):
    saldo = pegar_saldo()
    preco_btc = float(cliente_binance.get_symbol_ticker(symbol='BTCUSDT')['price'])
    preco_sol = float(cliente_binance.get_symbol_ticker(symbol='SOLUSDT')['price'])
    preco_eth = float(cliente_binance.get_symbol_ticker(symbol='ETHUSDT')['price'])
    total = saldo["USDT"] + saldo["BTC"] * preco_btc + saldo["SOL"] * preco_sol + saldo["ETH"] * preco_eth
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dados.setdefault("historico_patrimonio", []).append({"timestamp": timestamp, "saldo_total_usdt": total})
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
            try:
                t = datetime.strptime(item["timestamp"], "%Y-%m-%d %H:%M:%S")
                if t <= alvo:
                    return item["saldo_total_usdt"]
            except:
                continue
        return None
    antigo_24h = buscar_antigo(24)
    antigo_7d = buscar_antigo(24 * 7)
    if antigo_24h:
        variacao = ((atual - antigo_24h) / antigo_24h) * 100
        logging.info(f"Valorização em 24h: {variacao:.2f}%")
    if antigo_7d:
        variacao = ((atual - antigo_7d) / antigo_7d) * 100
        logging.info(f"Valorização em 7 dias: {variacao:.2f}%")

def pegar_dados(codigo, intervalo):
    try:
        candles = cliente_binance.get_klines(symbol=codigo, interval=intervalo, limit=100)
        df = pd.DataFrame(candles)
        df.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
        df = df[["close", "close_time"]]
        df["close"] = df["close"].astype(float)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        return df
    except:
        return pd.DataFrame()

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

def comprar_dividido_em_btc_eth(saldo_usdt):
    try:
        for moeda in ["BTCUSDT", "ETHUSDT"]:
            preco = float(cliente_binance.get_symbol_ticker(symbol=moeda)['price'])
            saldo_para_moeda = saldo_usdt / 2
            quantidade = saldo_para_moeda / preco
            quantidade = ajustar_quantidade(moeda, quantidade, saldo_para_moeda, preco)
            if float(quantidade) > 0:
                cliente_binance.create_order(symbol=moeda, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantidade)
                logging.info(f"Compra automática de {quantidade} {moeda} com {saldo_para_moeda:.2f} USDT")
    except Exception as e:
        logging.warning(f"Erro ao comprar BTC/ETH automaticamente: {e}")

def calcular_media_movel(df, periodo):
    return df['close'].rolling(window=periodo).mean()

def obter_preco_mais_alto(symbol):
    try:
        inicio = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        candles = cliente_binance.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1DAY, startTime=inicio)
        df = pd.DataFrame(candles)
        df[2] = pd.to_numeric(df[2], errors='coerce')
        return df[2].max()
    except:
        return 0

def mostrar_grafico(df, symbol):
    plt.figure(figsize=(12, 6))
    plt.plot(df['close_time'], df['close'], label='Preço')
    plt.plot(df['close_time'], df['media_curta'], label='Média 7', linestyle='--')
    plt.plot(df['close_time'], df['media_longa'], label='Média 40', linestyle='--')
    plt.title(f'{symbol} - Gráfico com Médias Móveis')
    plt.xlabel('Tempo')
    plt.ylabel('Preço')
    plt.legend()
    plt.grid()
    plt.tight_layout()
    nome_arquivo = f'grafico_{symbol}.png'
    plt.savefig(nome_arquivo)
    logging.info(f"Gráfico salvo como {nome_arquivo}")
    # subprocess.run(['xdg-open', nome_arquivo])  # para abrir no Linux automaticamente
    plt.close()

def estrategia(dados, symbol, posicao, saldo_disponivel_total, preco_atual):
    global precos_compra, stop_losses, take_profits
    if dados.empty or len(dados) < 41:
        return posicao

    dados["media_curta"] = calcular_media_movel(dados, 7)
    dados["media_longa"] = calcular_media_movel(dados, 40)

    logging.info(f"{symbol} - Média 7: {dados['media_curta'].iloc[-1]:.2f} | Média 40: {dados['media_longa'].iloc[-1]:.2f}")

    mostrar_grafico(dados, symbol)

    cruzou_para_cima = dados["media_curta"].iloc[-2] <= dados["media_longa"].iloc[-2] and dados["media_curta"].iloc[-1] > dados["media_longa"].iloc[-1]
    cruzou_para_baixo = dados["media_curta"].iloc[-2] >= dados["media_longa"].iloc[-2] and dados["media_curta"].iloc[-1] < dados["media_longa"].iloc[-1]

    if not posicao and cruzou_para_cima and saldo_disponivel_total > 10:
        logging.info(f"Sinal de compra para {symbol} detectado!")
        saldo_para_essa_moeda = saldo_disponivel_total / len(moedas)
        quantidade = saldo_para_essa_moeda / preco_atual
        quantidade = ajustar_quantidade(symbol, quantidade, saldo_para_essa_moeda, preco_atual)
        if float(quantidade) > 0:
            try:
                cliente_binance.create_order(symbol=symbol, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=quantidade)
                logging.info(f"Compra executada de {quantidade} {symbol} a {preco_atual:.2f} USDT")
                precos_compra[symbol] = preco_atual
                stop_losses[symbol] = preco_atual * (1 - percentual_stop_loss)
                preco_alvo = obter_preco_mais_alto(symbol)
                take_profits[symbol] = preco_alvo * (1 + percentual_take_profit) if preco_alvo else 0
                return True
            except Exception as e:
                logging.warning(f"Erro na compra de {symbol}: {e}")

    elif posicao:
        ativo = symbol.replace("USDT", "")
        saldo_ativo = float(cliente_binance.get_asset_balance(asset=ativo)['free'])
        quantidade = ajustar_quantidade(symbol, saldo_ativo, saldo_ativo, preco_atual)

        if preco_atual <= stop_losses[symbol]:
            try:
                cliente_binance.create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantidade)
                logging.info(f"Stop-loss acionado! Venda de {quantidade} {symbol} a {preco_atual:.2f} USDT")
                return False
            except Exception as e:
                logging.warning(f"Erro no stop-loss de {symbol}: {e}")

        elif preco_atual >= take_profits[symbol] or cruzou_para_baixo:
            try:
                cliente_binance.create_order(symbol=symbol, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=quantidade)
                logging.info(f"Venda realizada: {quantidade} {symbol} a {preco_atual:.2f} USDT (take-profit/cruzamento)")
                return False
            except Exception as e:
                logging.warning(f"Erro na venda de {symbol}: {e}")

    return posicao
def atualizar_historico(dados):
    """Atualiza o histórico de patrimônio com o saldo atual."""
    saldo = pegar_saldo()
    preco_btc = float(cliente_binance.get_symbol_ticker(symbol='BTCUSDT')['price'])
    preco_sol = float(cliente_binance.get_symbol_ticker(symbol='SOLUSDT')['price'])
    
    total_usdt = (
        saldo["USDT"] + 
        saldo["BTC"] * preco_btc + 
        saldo["SOL"] * preco_sol
    )
    
    registro = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "saldo_total_usdt": total_usdt
    }
    
    dados["historico_patrimonio"].append(registro)
    dados["historico_patrimonio"] = dados["historico_patrimonio"][-168:]  # Mantém apenas 7 dias (24h * 7)
    
    return dados
dados_salvos = {
    "posicoes": {moeda: False for moeda in moedas},
    "precos_compra": {moeda: 0 for moeda in moedas},
    "stop_losses": {moeda: 0 for moeda in moedas},
    "take_profits": {moeda: 0 for moeda in moedas},
    "historico_patrimonio": []  # ← Garanta que esta lista existe!
}
# Loop principal
while True:
    saldo = pegar_saldo()
    preco_btc = float(cliente_binance.get_symbol_ticker(symbol='BTCUSDT')['price'])
    preco_sol = float(cliente_binance.get_symbol_ticker(symbol='SOLUSDT')['price'])
    preco_eth = float(cliente_binance.get_symbol_ticker(symbol='ETHUSDT')['price'])
    total_usdt = saldo['USDT'] + saldo['BTC'] * preco_btc + saldo['SOL'] * preco_sol + saldo['ETH'] * preco_eth

    logging.info("\nResumo do saldo:")
    logging.info(f"USDT: {saldo['USDT']:.2f}")
    logging.info(f"BTC: {saldo['BTC']} (≈ {saldo['BTC'] * preco_btc:.2f} USDT)")
    logging.info(f"SOL: {saldo['SOL']} (≈ {saldo['SOL'] * preco_sol:.2f} USDT)")
    logging.info(f"ETH: {saldo['ETH']} (≈ {saldo['ETH'] * preco_eth:.2f} USDT)")
    logging.info(f"Total estimado em USDT: {total_usdt:.2f}\n")

    dados_salvos = carregar_dados()
    dados_salvos = atualizar_historico(dados_salvos)
    mostrar_valorizacao(dados_salvos)

    if not any(posicoes.values()) and saldo['USDT'] > 20:
        logging.info("Sem posições abertas. Comprando BTC e ETH com o saldo disponível.")
        comprar_dividido_em_btc_eth(saldo['USDT'])
        saldo = pegar_saldo()

    for moeda in moedas:
        df = pegar_dados(moeda, periodo_candle)
        if df.empty:
            continue
        preco_atual = df['close'].iloc[-1]
        posicoes[moeda] = estrategia(df, moeda, posicoes.get(moeda, False), saldo['USDT'], preco_atual)

    dados_salvos["posicoes"] = posicoes
    dados_salvos["precos_compra"] = precos_compra
    dados_salvos["stop_losses"] = stop_losses
    dados_salvos["take_profits"] = take_profits

    salvar_dados(dados_salvos)
    logging.info("Aguardando próxima verificação...")
    time.sleep(intervalo_verificacao)
