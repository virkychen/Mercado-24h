import os
import random
import threading
import time
from queue import Queue
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")  # para session (demo)

# Evita cache de template/static durante testes (reduz chance de ver "pagina antiga")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Configurações de resiliência
PAYMENT_APPROVAL_RATE = float(os.getenv("PAYMENT_APPROVAL_RATE", "0.7"))
PAYMENT_TIMEOUT = float(os.getenv("PAYMENT_TIMEOUT", "5.0"))
PAYMENT_MAX_RETRIES = int(os.getenv("PAYMENT_MAX_RETRIES", "3"))
PAYMENT_DELAY_SECONDS = float(os.getenv("PAYMENT_DELAY_SECONDS", "0.0"))
PORT = int(os.getenv("PORT", "5000"))
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "1234")

# Estado da fila e eventos
event_queue = Queue()
cart = []
released_orders = set()
event_log = []

# Simulação de cenário
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "normal")  # normal, timeout, fallback_only


# ============== FUNDAMENTOS DE RESILIÊNCIA ==============

def get_user_display_name(email):
    """Retorna nome de exibição a partir do e-mail (parte antes de @)."""
    if not email:
        return ""
    return str(email).split("@", 1)[0].strip()

def log_event(event_type, payload):
    """Publica evento na fila e no log"""
    event = {
        "type": event_type, 
        "payload": payload, 
        "createdAt": int(time.time() * 1000)
    }
    event_queue.put(event)
    event_log.append(event)
    print(f"[EVENT] {event_type}: {payload}")


def publish_event(event_type, payload):
    """Alias para compatibilidade"""
    log_event(event_type, payload)


def retry_with_exponential_backoff(max_retries=3, base_delay=0.2, timeout_seconds=None):
    """
    Decorador: RETRY com backoff exponencial e TIMEOUT
    
    - Tenta a operação max_retries vezes
    - A cada falha, aguarda base_delay * (2^tentativa) segundos
    - Se timeout_seconds, interrompe se passar desse tempo
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            start_time = time.time()
            
            for attempt in range(1, max_retries + 1):
                try:
                    if timeout_seconds:
                        elapsed = time.time() - start_time
                        if elapsed > timeout_seconds:
                            raise TimeoutError(f"Timeout global de {timeout_seconds}s excedido")
                    
                    result = func(*args, **kwargs)
                    if attempt > 1:
                        print(f"[RETRY] Sucesso na tentativa {attempt}")
                    return result, attempt
                    
                except Exception as e:
                    last_exception = e
                    print(f"[RETRY] Tentativa {attempt}/{max_retries} falhou: {str(e)}")
                    
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))  # Backoff exponencial
                        print(f"[RETRY] Aguardando {delay:.1f}s antes da próxima tentativa...")
                        time.sleep(delay)
            
            # Se chegou aqui, esgotou todas as tentativas
            print(f"[RETRY] Esgotadas {max_retries} tentativas")
            raise last_exception
        
        return wrapper
    return decorator


# ============== SERVIÇO DE PAGAMENTO ==============

@retry_with_exponential_backoff(max_retries=PAYMENT_MAX_RETRIES, base_delay=0.2)
def attempt_payment_with_retry():
    """
    Tenta realizar pagamento com simulação de approval rate
    Retorna True/False baseado em PAYMENT_APPROVAL_RATE
    """
    # TIMEOUT (modo de simulação): garante um erro real para retornar 504.
    # Observacao: evitamos sleep duplo (PAYMENT_DELAY_SECONDS + TIMEOUT),
    # porque o cenario de timeout ja configura um delay separado na UI.
    if SIMULATION_MODE == "timeout":
        print("[PAYMENT] Simulação: forçando timeout...")
        time.sleep(PAYMENT_TIMEOUT)
        raise TimeoutError("Pagamento excedeu o tempo limite (simulado).")

    # Simula delay (para testar comportamento com latencia, sem timeout real)
    if PAYMENT_DELAY_SECONDS > 0:
        print(f"[PAYMENT] Simulando delay de {PAYMENT_DELAY_SECONDS}s...")
        time.sleep(PAYMENT_DELAY_SECONDS)
    
    # Simula fallback forçado (0% de aprovação)
    if SIMULATION_MODE == "fallback_only":
        raise Exception("Simula falha de pagamento - força fallback")
    
    # Normal: random approval
    if random.random() <= PAYMENT_APPROVAL_RATE:
        return True
    else:
        raise Exception(f"Pagamento recusado (taxa de aprovação: {PAYMENT_APPROVAL_RATE * 100:.0f}%)")


def queue_worker():
    """
    Worker assíncrono: Processa fila de eventos
    Implementa FALLBACK para falhas de pagamento
    """
    while True:
        event = event_queue.get()
        event_type = event["type"]
        payload = event["payload"]

        if event_type == "pedido_confirmado":
            print(f"[QUEUE] Pedido confirmado: {payload['orderId']}")
            
        elif event_type == "compra_realizada":
            order_id = payload["orderId"]
            released_orders.add(order_id)
            print(f"[QUEUE] 💳 Compra realizada! Liberando saida: {order_id}")
            publish_event("saida_liberada", {
                "orderId": order_id, 
                "status": "ok",
                "reason": "payment_approved"
            })
            
        elif event_type == "compra_falhou":
            order_id = payload["orderId"]
            print(f"[QUEUE] ❌ FALLBACK acionado para {order_id}")
            print(f"[QUEUE] Tentativas de pagamento: {payload.get('attempts', 0)}")
            # FALLBACK: Estratégia de negócio - permitir saída com verificação manual
            publish_event("saida_liberada_manual", {
                "orderId": order_id,
                "status": "pending_review",
                "reason": "payment_failed_after_retries",
                "attempts": payload.get("attempts", 0),
                "note": "Aguardando verificação de supervisor"
            })

        event_queue.task_done()


@app.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    if not body.get("email"):
        return jsonify({"ok": False, "error": "email obrigatorio"}), 400
    if not body.get("senha"):
        return jsonify({"ok": False, "error": "senha obrigatoria"}), 400
    if str(body.get("senha")) != LOGIN_PASSWORD:
        return jsonify({"ok": False, "error": "senha invalida"}), 401
    session["user_email"] = body["email"]
    session.pop("entrada_liberada", None)
    return jsonify({"ok": True, "etapa": "login_conta"})


@app.get("/")
def index():
    if session.get("user_email"):
        return redirect(url_for("produtos_page"))
    return redirect(url_for("login_page"))


@app.get("/login")
def login_page():
    if session.get("user_email"):
        return redirect(url_for("entrada_page"))
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/produtos")
def produtos_page():
    if not session.get("user_email"):
        return redirect(url_for("login_page"))
    return render_template(
        "produtos.html",
        user_email=session.get("user_email"),
        user_name=get_user_display_name(session.get("user_email")),
        entrada_liberada=bool(session.get("entrada_liberada")),
    )


@app.get("/entrada")
def entrada_page():
    if not session.get("user_email"):
        return redirect(url_for("login_page"))
    if session.get("entrada_liberada"):
        return redirect(url_for("produtos_page"))
    return render_template(
        "entrada.html",
        user_email=session.get("user_email"),
        user_name=get_user_display_name(session.get("user_email")),
    )


@app.post("/entrada/liberar")
def liberar_entrada():
    body = request.get_json(silent=True) or {}
    if not body.get("qrCode"):
        return jsonify({"ok": False, "error": "qrCode obrigatorio"}), 400
    session["entrada_liberada"] = True
    return jsonify({"ok": True, "etapa": "qr_code_entrada_liberada"})


@app.post("/carrinho/adicionar")
def adicionar_carrinho():
    body = request.get_json(silent=True) or {}
    required = ["productId", "nome", "preco"]
    if any(field not in body for field in required):
        return jsonify({"ok": False, "error": "productId, nome e preco sao obrigatorios"}), 400

    quantidade = int(body.get("quantidade", 1))
    if quantidade <= 0:
        return jsonify({"ok": False, "error": "quantidade deve ser maior que zero"}), 400

    item = {
        "productId": body["productId"],
        "nome": body["nome"],
        "preco": float(body["preco"]),
        "quantidade": quantidade,
    }
    cart.append(item)
    return jsonify({"ok": True, "etapa": "incluir_produto", "itensNoCarrinho": len(cart)})


@app.post("/carrinho/remover")
def remover_carrinho():
    body = request.get_json(silent=True) or {}
    product_id = body.get("productId")
    if not product_id:
        return jsonify({"ok": False, "error": "productId obrigatorio"}), 400

    quantidade_remover = int(body.get("quantidade", 1))
    if quantidade_remover <= 0:
        return jsonify({"ok": False, "error": "quantidade deve ser maior que zero"}), 400

    restante = quantidade_remover
    novo_cart = []

    for item in cart:
        if item["productId"] != product_id or restante == 0:
            novo_cart.append(item)
            continue

        if item["quantidade"] <= restante:
            restante -= item["quantidade"]
            continue

        item_atualizado = dict(item)
        item_atualizado["quantidade"] = item["quantidade"] - restante
        restante = 0
        novo_cart.append(item_atualizado)

    if restante == quantidade_remover:
        return jsonify({"ok": False, "error": "produto nao encontrado no carrinho"}), 404

    cart.clear()
    cart.extend(novo_cart)
    return jsonify({"ok": True, "etapa": "remover_produto", "itensNoCarrinho": len(cart)})


@app.post("/pedido/confirmar")
def confirmar_pedido():
    if not cart:
        return jsonify({"ok": False, "etapa": "nenhum_produto", "message": "Nenhum produto no carrinho"}), 400

    itens = list(cart)
    order_id = f"ord_{int(time.time() * 1000)}"
    total = round(sum(i["preco"] * i["quantidade"] for i in itens), 2)
    publish_event("pedido_confirmado", {"orderId": order_id, "total": total})

    return jsonify(
        {
            "ok": True,
            "etapa": "confirmar_pedido",
            "orderId": order_id,
            "total": total,
            "itens": itens,
        }
    )


@app.post("/pagamento/realizar")
def realizar_pagamento():
    """
    Endpoint: Realiza pagamento com RETRY, TIMEOUT e FALLBACK
    
    RETRY: Tenta 3x com backoff exponencial
    TIMEOUT: Máximo de 5s por tentativa
    FALLBACK: Se falhar 3x, aciona `compra_falhou` (saída manual)
    """
    body = request.get_json(silent=True) or {}
    order_id = body.get("orderId")
    total = body.get("total")
    if not order_id or total is None:
        return jsonify({"ok": False, "error": "orderId e total sao obrigatorios"}), 400

    try:
        # Chamada com RETRY + TIMEOUT
        approved, attempts = attempt_payment_with_retry()
        
        # Se chegou aqui, pagamento aprovado
        publish_event("compra_realizada", {
            "orderId": order_id, 
            "total": total, 
            "attempts": attempts
        })
        # Limpa o carrinho apenas quando pagamento foi aprovado.
        cart.clear()
        
        return jsonify({
            "ok": True, 
            "etapa": "compra_realizada", 
            "orderId": order_id, 
            "tentativas": attempts,
            "total": total
        }), 200
        
    except TimeoutError as e:
        # TIMEOUT: Trata como erro, mas ainda pode ter tido tentativas
        print(f"[TIMEOUT] Pagamento excedeu tempo limite: {str(e)}")
        publish_event("compra_falhou_timeout", {
            "orderId": order_id,
            "total": total,
            "reason": str(e)
        })
        
        return jsonify({
            "ok": False, 
            "etapa": "timeout_pagamento",
            "orderId": order_id,
            "error": "Timeout: pagamento demorou demais"
        }), 504  # Gateway Timeout
        
    except Exception as e:
        # FALLBACK: Esgotadas as tentativas
        attempts = PAYMENT_MAX_RETRIES  # Esgotou todas
        print(f"[FALLBACK] Acionando fallback após {attempts} tentativas: {str(e)}")
        
        publish_event("compra_falhou", {
            "orderId": order_id,
            "total": total,
            "attempts": attempts,
            "reason": str(e)
        })
        
        return jsonify({
            "ok": False, 
            "etapa": "falha_ao_pagar",
            "orderId": order_id,
            "tentativas": attempts,
            "error": "Pagamento recusado após tentar 3x",
            "fallback": "saida_liberada_manual"
        }), 402  # Payment Required


@app.get("/saida/status/<order_id>")
def status_saida(order_id):
    return jsonify({"ok": True, "orderId": order_id, "saidaLiberada": order_id in released_orders})


@app.get("/eventos")
def eventos():
    return jsonify({"ok": True, "total": len(event_log), "eventos": event_log})


# ============== ENDPOINTS DE SIMULAÇÃO/DEBUG ==============

@app.post("/config/payment-rate")
def config_payment_rate():
    """
    Altera taxa de aprovação de pagamento em tempo real
    POST /config/payment-rate {"rate": 0.3}
    """
    global PAYMENT_APPROVAL_RATE
    body = request.get_json(silent=True) or {}
    rate = body.get("rate", PAYMENT_APPROVAL_RATE)
    PAYMENT_APPROVAL_RATE = float(rate)
    print(f"[CONFIG] PAYMENT_APPROVAL_RATE alterada para {PAYMENT_APPROVAL_RATE * 100:.0f}%")
    return jsonify({
        "ok": True, 
        "message": f"Taxa de aprovação alterada para {PAYMENT_APPROVAL_RATE * 100:.0f}%"
    })


@app.post("/config/simulation-mode")
def config_simulation_mode():
    """
    Altera modo de simulação
    Modos: "normal", "timeout", "fallback_only"
    POST /config/simulation-mode {"mode": "timeout"}
    """
    global SIMULATION_MODE, PAYMENT_DELAY_SECONDS
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "normal")
    delay = body.get("delay", 0)
    
    SIMULATION_MODE = mode
    PAYMENT_DELAY_SECONDS = float(delay)
    
    msg = f"Modo: {SIMULATION_MODE}"
    if delay > 0:
        msg += f", Delay: {delay}s"
    
    print(f"[CONFIG] {msg}")
    return jsonify({
        "ok": True,
        "message": msg,
        "simulation_mode": SIMULATION_MODE,
        "payment_delay": PAYMENT_DELAY_SECONDS
    })


@app.get("/config/current")
def config_current():
    """Retorna configuração atual de resiliência"""
    return jsonify({
        "ok": True,
        "payment_approval_rate": PAYMENT_APPROVAL_RATE,
        "payment_max_retries": PAYMENT_MAX_RETRIES,
        "payment_timeout": PAYMENT_TIMEOUT,
        "payment_delay": PAYMENT_DELAY_SECONDS,
        "simulation_mode": SIMULATION_MODE
    })


@app.post("/teste/cenario")
def teste_cenario():
    """
    Configura um cenário de teste específico
    
    POST /teste/cenario {"cenario": "retry"}
    - "retry": Taxa 30% (força retries)
    - "fallback": Taxa 0% (força fallback após 3x)
    - "timeout": Delay 6s (força timeout)
    """
    body = request.get_json(silent=True) or {}
    cenario = body.get("cenario", "retry")
    
    configs = {
        "retry": {
            "rate": 0.3,
            "mode": "normal",
            "delay": 0.0,
            "message": "Cenário RETRY: 30% de aprovação, vai retentar 3x até sucesso"
        },
        "fallback": {
            "rate": 0.0,
            "mode": "normal",
            "delay": 0.0,
            "message": "Cenário FALLBACK: 0% de aprovação, vai falhar 3x e acionar fallback"
        },
        "timeout": {
            "rate": 0.7,
            "mode": "timeout",
            "delay": 6.0,
            "message": "Cenário TIMEOUT: Delay de 6s (timeout 5s), vai forçar timeout"
        }
    }
    
    if cenario not in configs:
        return jsonify({
            "ok": False,
            "error": f"Cenário desconhecido. Use: {', '.join(configs.keys())}"
        }), 400
    
    cfg = configs[cenario]
    PAYMENT_APPROVAL_RATE = float(cfg["rate"])
    
    # Global update (workaround)
    globals()['PAYMENT_APPROVAL_RATE'] = float(cfg["rate"])
    globals()['SIMULATION_MODE'] = cfg["mode"]
    globals()['PAYMENT_DELAY_SECONDS'] = float(cfg["delay"])
    
    print(f"[TEST] Cenário configurado: {cenario}")
    
    return jsonify({
        "ok": True,
        "message": cfg["message"],
        "cenario": cenario,
        "config_aplicada": {
            "payment_approval_rate": cfg["rate"],
            "simulation_mode": cfg["mode"],
            "payment_delay": cfg["delay"]
        }
    })


if __name__ == "__main__":
    worker = threading.Thread(target=queue_worker, daemon=True)
    worker.start()
    app.run(host="0.0.0.0", port=PORT)

