from __future__ import annotations

import asyncio
import grpc
import random
import string
import google.protobuf.empty_pb2
import colored

from colored import stylize
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.completion import FuzzyWordCompleter

import protos.request_pb2 as protos
import protos.request_pb2_grpc as grpc_protos


MANUAL_MODE = 'manual'
AUTOMATIC_MODE = 'automatic'

CHANGE_NICKNAME_OPTION = 'Change nickname'
GO_TO_THE_GAME_OPTION = 'Start playing'
EXIT_OPTION = 'Exit'
CREATE_GAME_OPTION = 'Create game'
ENTER_GAME_OPTION = 'Enter game'

ALLOWED_NICKNAME_SYMBOLS = set(string.ascii_uppercase + string.ascii_lowercase + string.digits + '_')
GENERATED_NICKNAME_LENGTH = 8


class CommandsValidator(Validator):
    options: list[str] = []
    def __init__(self, options: list[str], **kwargs):
        self.options = options
        super().__init__(**kwargs)

    def validate(self, document):
        text = document.text
        if text and text not in self.options:
            raise ValidationError(
                message='Wrong command, choose one of the suggested ones from below',
                cursor_position=0
            )

class NicknameValidator(Validator):
    def validate(self, document):
        text = document.text
        if not text:
            raise ValidationError(message='Nickname cannot be empty', cursor_position=0)
        if not set(text) <= ALLOWED_NICKNAME_SYMBOLS:
            i = 0
            for i, c in enumerate(text):
                if c not in ALLOWED_NICKNAME_SYMBOLS:
                    break
            raise ValidationError(
                message="Incorrect nickname, allowed only ascii lowercase, uppercase, digits and '_'",
                cursor_position=i
            )

async def create_choice_field_fixed_toolbar(msg: str, options: list[str]) -> str:
    def bottom_toolbar():
        return [('class:bottom-toolbar', '\n'.join(['Possible options:'] + [">" + x for x in options]))]

    session = PromptSession(erase_when_done=True)
    with patch_stdout():
        return await session.prompt_async(
            msg,
            validator=CommandsValidator(options),
            validate_while_typing=True,
            completer=FuzzyWordCompleter(options),
            bottom_toolbar=bottom_toolbar,
        )

async def create_choice_field_with_game_info(msg: str, client: Client, options: list[str]) -> str:
    game = client.game
    if not game:
        return ""

    def bottom_toolbar():
        new_game = client.game
        if new_game:
            game = new_game
        strs = [
            f"Game id: {game.Id}",
            f"Status: {protos.GameStatus.Name(game.GameInfo)}",
            "=== List of players ==="
        ]
        for i, player in enumerate(game.Players):
            role = protos.Role.Name(player.Role)
            if player.CheckedBySheriff:
                role += ' (verified by sheriff)'
            cond = protos.Condition.Name(player.Condition)
            you = ", You" if client.name == player.Login else ""
            strs.append(
                f"{player.Login} (Player {i}{you}), Role: {role}, Condition: {cond}"
            )
        strs += ["=======================", 'Possible options:'] + [">" + x for x in options]
        return [('class:bottom-toolbar', '\n'.join(strs))]

    session = PromptSession(erase_when_done=True)
    with patch_stdout():
        return await session.prompt_async(
            msg,
            refresh_interval=0.1,
            validator=CommandsValidator(options),
            validate_while_typing=True,
            completer=FuzzyWordCompleter(options),
            bottom_toolbar=bottom_toolbar,
        )


async def create_text_field(msg: str, validator: Validator | None = None, default: str = "") -> str:
    session = PromptSession(erase_when_done=True)
    with patch_stdout():
        return await session.prompt_async(
            msg,
            default=default,
            validator=validator,
            validate_while_typing=True if validator else False,
        )

class Client:
    name: str = 'Palyer'
    mode: str = MANUAL_MODE
    game: protos.Game | None = None
    lock: asyncio.Lock

    # @staticmethod
    # async def chat_stub(stub):
    #     print("Connected to chat")
    #     async for msg in stub.GetChatMessages(google.protobuf.empty_pb2.Empty()):
    #         print(f"<{msg.PlayerName}(Player {msg.PlayerNumber})> {msg.Message}")

    def __init__(self):
        self.name = self.generate_ranmdom_name()
        self.lock = asyncio.Lock()

    async def run(self):
        server_addr = await create_text_field("Enter server address: ", default="localhost:5000")
        try:
            async with grpc.aio.insecure_channel(server_addr) as channel:
                stub = grpc_protos.ServerStub(channel)
                _ = await stub.Connect(google.protobuf.empty_pb2.Empty())
                while True:
                    choice = await create_choice_field_fixed_toolbar(
                        f'Your nickname: {self.name}\nEnter command: ',
                        [CHANGE_NICKNAME_OPTION, GO_TO_THE_GAME_OPTION, EXIT_OPTION]
                    )
                    if choice == CHANGE_NICKNAME_OPTION:
                        self.name = await create_text_field("Enter new nickname: ", NicknameValidator())
                    elif choice == GO_TO_THE_GAME_OPTION:
                        await self.enter_game(stub)
                    elif choice == EXIT_OPTION:
                        break
        except grpc.RpcError as err:
            if err.code() == grpc.StatusCode.UNAVAILABLE:
                print(stylize(f"Something went wrong, couldn't connect to the server '{server_addr}'", colored.fg("red")))
            elif err.code() == grpc.StatusCode.UNKNOWN:
                print(stylize(f"Server error: {err.debug_error_string()}", colored.fg("red")))
            else:
                raise

    async def update_game(self, stub: grpc_protos.ServerStub, game_id: str):
        async for game in stub.JoinGame(protos.JoinParams(User=self.name, GameId=game_id)):
            async with self.lock:
                if game.Status == protos.LobbyStatus.NOT_FOUND:
                    self.game = None
                    print(stylize(f"Game with id {game_id} not found", colored.fg("yellow")))
                elif game.Status == protos.LobbyStatus.FULL:
                    self.game = None
                    print(stylize(f"Game with id {game_id} is already full", colored.fg("yellow")))
                else:
                    self.game = game
        async with self.lock:
            self.game = None

    async def enter_game(self, stub: grpc_protos.ServerStub):
        game_id = await create_text_field(
            'Choose a game to join. To create a new game, leave the field empty.\nEnter game id: '
        )
        updater = asyncio.create_task(self.update_game(stub, game_id))
        while not self.game and not updater.done():
            await asyncio.sleep(1)
        # game_chat = asyncio.create_task(self.chat_stub(stub))
        while True:
            choice = await create_choice_field_with_game_info(
                f'Enter command: ',
                self,
                [EXIT_OPTION]
            )
            if (not choice and updater.done()) or choice == EXIT_OPTION:
                updater.cancel()
                return

    @staticmethod
    def generate_ranmdom_name() -> str:
        return ''.join(random.sample(ALLOWED_NICKNAME_SYMBOLS, GENERATED_NICKNAME_LENGTH))


async def main():
    c = Client()
    await c.run()

if __name__ == '__main__':
    asyncio.run(main())
