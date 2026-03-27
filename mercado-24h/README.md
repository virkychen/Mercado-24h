# Mercado 24h - Flask + Queue Simples

Arquitetura simples baseada no diagrama, usando:

- **Caixas lisas (sincrono)**: rotas HTTP no Flask.
- **Caixas listradas (assincrono)**: eventos em uma fila Python (`queue.Queue`) processados por worker em background.

## Mapeamento do diagrama

### Sincrono (HTTP)

1. Login da conta -> `POST /login`
2. QR code para liberar entrada -> `POST /entrada/liberar`
3. Incluir produto -> `POST /carrinho/adicionar`
4. Confirmar o pedido -> `POST /pedido/confirmar`
5. Realizar o pagamento -> `POST /pagamento/realizar`
6. Nenhum produto -> retorno de negocio em `POST /pedido/confirmar`
7. Falha ao pagar -> retorno de negocio em `POST /pagamento/realizar`

### Assincrono (Queue)

1. Fila -> evento `pedido_confirmado`
2. Compra realizada -> evento `compra_realizada`
3. Liberar a saida -> evento `saida_liberada`

## Estrutura

- `app.py`: API Flask e worker da fila.
- `requirements.txt`: dependencias Python.
- `Dockerfile`: imagem da aplicacao.
- `docker-compose.yml`: execucao local simplificada.

## Rodar local

### Opcao 1: Python direto

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Opcao 2: Docker

```bash
docker compose up --build
```

## Interface web de teste

Com a aplicacao rodando, abra `http://localhost:5000/`: uma tela enxuta com os passos do fluxo, botao **Fluxo completo** e uma linha de status (sem paineis de debug).

## Fluxo de teste

1. Login:

```bash
curl -X POST http://localhost:5000/login -H "Content-Type: application/json" -d "{\"email\":\"user@mercado.com\"}"
```

2. Liberar entrada por QR:

```bash
curl -X POST http://localhost:5000/entrada/liberar -H "Content-Type: application/json" -d "{\"qrCode\":\"QR-OK-123\"}"
```

3. Adicionar produto:

```bash
curl -X POST http://localhost:5000/carrinho/adicionar -H "Content-Type: application/json" -d "{\"productId\":\"p1\",\"nome\":\"Arroz\",\"preco\":25.9,\"quantidade\":1}"
```

4. Confirmar pedido:

```bash
curl -X POST http://localhost:5000/pedido/confirmar
```

5. Realizar pagamento (use `orderId` e `total` retornados na etapa anterior):

```bash
curl -X POST http://localhost:5000/pagamento/realizar -H "Content-Type: application/json" -d "{\"orderId\":\"ord_123\",\"total\":25.9}"
```

6. Consultar saida liberada:

```bash
curl http://localhost:5000/saida/status/ord_123
```
