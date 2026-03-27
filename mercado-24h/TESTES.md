# 📋 Guia de Testes - Mercadinho 24h

## ▶️ Como abrir e rodar o código (Windows / PowerShell)

### Pré-requisitos
- Python **3.10+** instalado (recomendado 3.12)
- (Opcional) Docker Desktop, se quiser rodar via container

### Opção A: Rodar com Python (recomendado)
1. Abra o terminal **na pasta `mercado-24h`** (onde está o `app.py`).
2. Crie e ative um ambiente virtual, instale dependências e rode:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

3. Abra no navegador: `http://localhost:5000/`

### Opção B: Rodar com Docker
1. Abra o terminal **na pasta `mercado-24h`**.
2. Suba o container:

```bash
docker compose up --build
```

3. Abra no navegador: `http://localhost:5000/`

### Se der erro (rápido)
- Se `python` não for reconhecido: instale Python e marque **“Add Python to PATH”**.
- Se a porta 5000 estiver ocupada: feche a app que estiver usando ou mude a porta no seu ambiente.

## Fundamentos de Resiliência Implementados

### 1. **RETRY** 🔄
Retentativa automática de operações falhadas com backoff exponencial.

**Implementação:**
- Máximo de 3 tentativas
- Delay: 0.2s → 0.4s → 0.8s (2^n exponencial)
- Timeout: 5 segundos por tentativa

**Cenário:** Taxa de aprovação em 30%

---

### 2. **TIMEOUT** ⏱️
Interrupção de operações que excedem o limite de tempo.

**Implementação:**
- Timeout global: 5 segundos
- Se operação passar disso, é tratada como erro
- Diferencia entre erro de timeout e falha de negócio

**Cenário:** Delay de 6 segundos na resposta

---

### 3. **FALLBACK** 🚫
Plano B quando retry falha todas as vezes.

**Implementação:**
- Após 3 tentativas falhadas, aciona fallback
- Fallback registra evento `compra_falhou`
- Estratégia: Libera saída manual para revisão (saída_liberada_manual)

**Cenário:** Taxa de 0% de aprovação

---

## 🎮 Como Testar

### Preparação
1. Inicie a aplicação seguindo a seção **"Como abrir e rodar o código"** acima.
2. Acesse: `http://localhost:5000/`

### Teste 1: RETRY

**Passo a passo:**

1. Clique em **"🔄 Configurar RETRY (30% aprovação)"**
   - Isso deixa apenas 30% de chance de pagamento ser aprovado
   - Vai precisar retentar várias vezes até sucesso

2. Complete o fluxo normalmente:
   - ✅ Login
   - ✅ Liberar entrada (QR)
   - ✅ Adicionar produto ao carrinho
   - ✅ Confirmar pedido

3. Clique em **"💳 Pagar"**
   - Aguarde o resultado
   - Você verá "tentativas": 1, 2 ou 3

4. **Esperado:**
   - Campo "tentativas" > 1 (mostrou que reofrou)
   - Status final: "ok" (conseguiu na 2ª ou 3ª tentativa)

**Validação nos eventos:**
- Procure por `compra_realizada` com `attempts: 2` ou `attempts: 3`
- Clique "📋 Ver eventos" para confirmar

---

### Teste 2: FALLBACK

**Passo a passo:**

1. Clique em **"⚠️ Configurar FALLBACK (0% aprovação)"**
   - Taxa de aprovação = 0%
   - 100% de falha garantida

2. Complete o fluxo:
   - ✅ Login
   - ✅ Liberar entrada (QR)
   - ✅ Adicionar produto ao carrinho
   - ✅ Confirmar pedido

3. Clique em **"💳 Pagar"**
   - Aguarde o resultado
   - Vai falhar 3 vezes

4. **Esperado:**
   - Status: "falha_ao_pagar"
   - Campo "tentativas": 3
   - Campo "fallback": "saida_liberada_manual"

**Validação nos eventos:**
- Procure por evento `compra_falhou` (pode demorar um pouco)
- Procure por `saida_liberada_manual` com "status": "pending_review"
- Clique "📋 Ver eventos" para ver a sequência completa

**Fluxo visual:**
```
pedido_confirmado 
  → ["fallback activado!"]
  → compra_falhou (3 tentativas)
  → saida_liberada_manual (aguardando revisão)
```

---

### Teste 3: TIMEOUT

**Passo a passo:**

1. Clique em **"🕐 Configurar TIMEOUT (6s delay)"**
   - Simula delay de 6 segundos na resposta
   - Timeout configurado = 5 segundos
   - Mais tempo de resposta que o timeout

2. Complete o fluxo:
   - ✅ Login
   - ✅ Liberar entrada (QR)
   - ✅ Adicionar produto ao carrinho
   - ✅ Confirmar pedido

3. Clique em **"💳 Pagar"**
   - Aguarde ~6 segundos
   - Você verá erro de timeout

4. **Esperado:**
   - Status HTTP: 504 (Gateway Timeout)
   - Erro: "timeout: pagamento demorou demais"
   - Evento: `compra_falhou_timeout`

**Validação:**
- Observe que timeout foi disparado antes de completar os 6 segundos
- Clique "📋 Ver eventos" para ver `compra_falhou_timeout`

---

## 📊 Leitura de Eventos

Cada teste deixa rastros nos eventos. Clique **"📋 Ver eventos"** para ver:

### Sucesso (Retry funcionou):
```json
{
  "type": "pedido_confirmado",
  "payload": { "orderId": "ord_123", "total": 25.90 }
},
{
  "type": "compra_realizada",
  "payload": { 
    "orderId": "ord_123", 
    "total": 25.90, 
    "attempts": 2  // ← Fez 2 tentativas
  }
},
{
  "type": "saida_liberada",
  "payload": { "orderId": "ord_123", "status": "ok", "reason": "payment_approved" }
}
```

### Fallback (Todas as tentativas falharam):
```json
{
  "type": "compra_falhou",
  "payload": { 
    "orderId": "ord_123", 
    "attempts": 3,  // ← Esgotou as 3 tentativas
    "reason": "Pagamento recusado..."
  }
},
{
  "type": "saida_liberada_manual",
  "payload": { 
    "orderId": "ord_123",
    "status": "pending_review",  // ← Aguardando revisão
    "reason": "payment_failed_after_retries",
    "attempts": 3
  }
}
```

### Timeout:
```json
{
  "type": "compra_falhou_timeout",
  "payload": { 
    "orderId": "ord_123",
    "reason": "Timeout global de 5.0s excedido"
  }
}
```

---

## 🔧 Configurações Avançadas (via curl)

### Alterar taxa de aprovação em tempo real:
```bash
curl -X POST http://localhost:5000/config/payment-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.5}'
```

### Simular modo específico:
```bash
# Normal
curl -X POST http://localhost:5000/config/simulation-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "normal"}'

# Timeout (com 6s de delay)
curl -X POST http://localhost:5000/config/simulation-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "timeout", "delay": 6.0}'

# Fallback (0% aprovação)
curl -X POST http://localhost:5000/config/simulation-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "fallback_only"}'
```

### Ver configuração atual:
```bash
curl http://localhost:5000/config/current | python -m json.tool
```

---

## ✅ Checklist de Testes

- [ ] Teste RETRY completado (2-3 tentativas, status ok)
- [ ] Teste FALLBACK completado (3 tentativas, fallback acionado)
- [ ] Teste TIMEOUT completado (erro 504 após 5s)
- [ ] Validou eventos em cada teste
- [ ] Resetou para modo normal

---

## 📝 Documentação da Arquitetura

### Estrutura de Componentes

```
app.py
├── Rotas Síncronas (HTTP)
│   ├── POST /login
│   ├── POST /entrada/liberar
│   ├── POST /carrinho/adicionar
│   ├── POST /pedido/confirmar
│   └── POST /pagamento/realizar  ← COM RETRY/TIMEOUT/FALLBACK
├── Rotas Assíncronas (Fila)
│   ├── queue_worker() [thread daemon]
│   └── event_queue (Queue Python)
└── Endpoints de Teste
    ├── POST /teste/cenario
    ├── POST /config/payment-rate
    ├── POST /config/simulation-mode
    └── GET /config/current
```

### Fluxo de Pagamento com Resiliência

```
POST /pagamento/realizar
    ↓
[RETRY DECORATOR]
    ├─ Tentativa 1  → falha? delay 0.2s
    ├─ Tentativa 2  → falha? delay 0.4s
    └─ Tentativa 3  → falha? → FALLBACK
        ↓
    ✓ Sucesso → compra_realizada
    ✗ Falha   → compra_falhou + saida_liberada_manual
```

### Tratamento de Timeout

```
attempt_payment_with_retry()
    ↓
[TIMEOUT CHECK]
    ├─ Tempo total < 5s? ✓ continua
    └─ Tempo total > 5s? ✗ TimeoutError
        ↓
        compra_falhou_timeout (evento especial)
```

---

## 🐛 Troubleshooting

**Pagamento sempre sucede?**
- Certifique-se de clicar no botão de cenário antes de pagar
- Verifique a configuração atual: "📋 Ver configuração"

**Timeout não acontece?**
- Verifique que clicou "🕐 Configurar TIMEOUT" primeiro
- Observe o delay na resposta (deve ser notavelmente lento)

**Eventos desaparecem?**
- Todos os eventos são persistidos em memória durante a sessão
- Se reiniciar a app, o histórico é perdido

---

## 📚 Referências

- **Retry Pattern**: Tentativa automática com backoff exponencial
- **Timeout Pattern**: Limite máximo de espera por resposta
- **Fallback Pattern**: Ação alternativa quando retry esgota

Padrões baseados em: ["Release It!" - Michael Nygard](https://pragprog.com/titles/mnee2/release-it-second-edition/)
