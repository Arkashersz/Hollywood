import argparse
from .batches import authorize_batch, next_batch


def main():
    parser = argparse.ArgumentParser(description="TikTok Studio — handoff de lotes ao Claude Code")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next", help="Mostra o próximo lote autorizado")
    auth = sub.add_parser("authorize", help="Registra autorização humana de um lote")
    auth.add_argument("batch_id")
    args = parser.parse_args()
    if args.command == "authorize":
        folder = authorize_batch(args.batch_id)
        print(f"Lote autorizado: {folder / 'task.md'}")
        return
    folder = next_batch()
    if folder is None:
        print("Nenhum lote autorizado aguardando Claude Code.")
        return
    print(f"Lote disponível: {folder / 'task.md'}")
    print("No workspace, execute `claude --chrome` e solicite a execução desse task.md com a skill holywood.")

if __name__ == "__main__": main()
