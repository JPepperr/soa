from __future__ import annotations

import asyncio
import grpc
import google.protobuf.empty_pb2
import logging
from configparser import ConfigParser
import string
import random

from aiologger import Logger
from concurrent import futures

import protos.request_pb2 as protos
import protos.request_pb2_grpc as grpc_protos


ALLOWED_GAME_ID_SYMBOLS = set(string.ascii_uppercase + string.ascii_lowercase + string.digits)
GENERATED_GAME_ID_LENGTH = 8

VISIBLE_ALLIES_ROLES = [protos.Role.MAFIA, protos.Role.SHERIFF]


class Player:
    login: str
    role: protos.Role
    condition: protos.Condition
    checked_by_sheriff: bool

    def __init__(self, login: str):
        self.login = login
        self.role = protos.Role.UNKNOWN
        self.condition = protos.Condition.ALIVE
        self.checked_by_sheriff = False


class Game:
    id: str
    status: protos.LobbyStatus
    info: protos.GameStatus
    are_sheriff_results_shown: bool
    players: list[Player]
    lock: asyncio.Lock
    deleted: bool
    config: ConfigParser
    total_players_cnt: int

    def __init__(self, config: ConfigParser):
        self.id = self.generate_game_id()
        self.status = protos.LobbyStatus.HAVE_SLOTS
        self.info = protos.GameStatus.NOT_STARTED
        self.are_sheriff_results_shown = False
        self.players = []
        self.lock = asyncio.Lock()
        self.deleted = False
        self.config = config
        self.total_players_cnt = int(config['Mafia']) + int(config['Sheriff']) + int(config['Civilian'])

    def get_player_info(self, for_user: Player, about: Player) -> protos.Player:
        player = protos.Player(
            Login=about.login,
            Role=protos.Role.UNKNOWN,
            Condition=about.condition,
            CheckedBySheriff=False
        )
        if self.info == protos.GameStatus.ENDED:
            player.Role = about.role
            player.CheckedBySheriff = about.checked_by_sheriff
            return player
        if self.are_sheriff_results_shown and (about.role == protos.Role.SHERIFF or about.checked_by_sheriff):
            player.Role = about.role
            player.CheckedBySheriff = about.checked_by_sheriff
            return player
        if for_user.role in VISIBLE_ALLIES_ROLES and for_user.role == about.role:
            player.Role = about.role
        return player

    async def get_game_info(self, user_id: int) -> protos.Game:
        game = protos.Game(Id=self.id, Status=self.status, GameInfo=self.info)
        cur_player = None
        for player in self.players:
            if player.login == user_id:
                cur_player = player
                break
        if not cur_player:
            raise ValueError(f"Player {user_id} not found in game {self.id}")
        for user in self.players:
            game.Players.append(self.get_player_info(cur_player, user))
        return game

    @staticmethod
    def generate_game_id() -> str:
        return ''.join(random.sample(ALLOWED_GAME_ID_SYMBOLS, GENERATED_GAME_ID_LENGTH))


class Server(grpc_protos.ServerServicer):
    config: ConfigParser
    log: Logger
    games: dict[str, Game]
    deletions_lock: asyncio.Lock
    total_players: int

    def __init__(self, config: ConfigParser):
        self.config = config
        self.log = Logger.with_default_handlers(name='mafia-server')
        self.games = {}
        self.deletions_lock = asyncio.Lock()

    async def Connect(self, request, context):
        return google.protobuf.empty_pb2.Empty()

    async def GetChatMessages(self, request, context):
        cnt = 0
        while True:
            self.log.info(f"done {cnt}")
            yield protos.ChatMessage(PlayerNumber=100, PlayerName="User", Message=f"msg{cnt}")
            cnt += 1
            await asyncio.sleep(5)

    async def JoinGame(self, request, context):
        game_id = request.GameId
        user_id = request.User
        if game_id == "":
            new_game = Game(self.config['game.roles'])
            game_id = new_game.id
            self.games[game_id] = new_game
        game = self.games.get(game_id, None)
        if not game:
            yield protos.Game(Id=game_id, Status=protos.LobbyStatus.NOT_FOUND)
            return
        async with game.lock:
            if game.deleted:
                yield protos.Game(Id=game_id, Status=protos.LobbyStatus.NOT_FOUND)
                return
            if game.status == protos.LobbyStatus.FULL:
                yield protos.Game(Id=game_id, Status=protos.LobbyStatus.FULL)
                return
            game.players.append(Player(user_id))
            if game.total_players_cnt == len(game.players):
                game.status = protos.LobbyStatus.FULL
        try:
            while game.info != protos.GameStatus.ENDED:
                yield await game.get_game_info(user_id)
                await asyncio.sleep(float(self.config['server.settings']['UpdateFrequency']))
        finally:
            async with game.lock:
                if game.info == protos.NOT_STARTED:
                    for i, p in enumerate(game.players):
                        if p.login == user_id:
                            game.players.pop(i)
                            break

async def serve():
    config = ConfigParser()
    config.read('config.ini')

    settings = config['server.settings']
    port = settings['Port']
    max_workers = int(settings['MaxWorkers'])
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=max_workers))

    grpc_protos.add_ServerServicer_to_server(Server(config), server)
    server.add_insecure_port('[::]:' + port)
    await server.start()
    print("Server started, listening on " + port)
    await server.wait_for_termination()

if __name__ == '__main__':
    asyncio.run(serve())
