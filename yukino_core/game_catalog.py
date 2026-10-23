"""Supported original game workers and their deliberately narrow AI capability."""
EXPANSION = {
 'x_connect4':'四子棋','x_tictactoe':'井字棋','x_gomoku':'五子棋','x_reversi':'黑白棋',
 'x_minesweeper':'扫雷','x_sliding':'滑块拼图','x_2048':'2048','x_hangman':'吊小人',
 'x_battleship':'海战','x_yahtzee':'快艇骰子','x_highlow':'高低牌','x_knight':'骑士巡游',
 'x_lights':'熄灯游戏','x_sokoban':'推箱子','x_maze':'迷宫','x_wordchain':'成语接龙',
 'x_arithmetic':'口算挑战','x_twentyfour':'24点','x_trivia':'知识问答','x_anagram':'乱序猜词','x_memory':'翻牌记忆'}
INTERACTION = dict(i_turtle='海龟汤',i_buzzer='抢答',i_story='续写',i_topics='话题箱',
 i_truth='真心冒险',i_undercover='谁是卧底',i_guess='你说我猜',i_teams='选队',
 i_dots='点格棋',i_nim='取石子',i_set='集合牌',i_auction='虚拟拍卖')
OTHER = dict(s_sudoku='数独',s_nonogram='数织',s_expedition='远征',
 wordle='猜词',guess_number='猜数字',rps='猜拳',m_uno='UNO',m_mahjong='麻将训练')
GAMES = {**EXPANSION,**INTERACTION,**OTHER}
BOARDS = {'x_connect4','x_tictactoe','x_gomoku','x_reversi'}
MULTIPLAYER = set(INTERACTION)|BOARDS|{'m_uno'}
AMBIENT = {'i_undercover','i_buzzer','i_truth'}
FIELDS = [
 dict(key='ai_enabled',type='boolean',label='允许模型参与本游戏',default=True,
      description='模型作为独立玩家或单人游戏搭档；不会把其他玩家的词语、手牌或谜底交给它。'),
 dict(key='ai_auto_join',type='boolean',label='开新局时自动邀请AI',default=False,
      description='也可在房间内发送 加入AI；加入后按轮次自动行动。创建/开始后加“纯玩家”可只玩真人局。'),
 dict(key='ai_turn_limit',type='integer',label='每条消息最多连续AI动作',default=3,min=1,max=6)]


def capability(pid):
    if pid not in GAMES:return None
    return dict(version=1,mode='player' if pid in MULTIPLAYER or pid=='rps' else 'partner',
                ambient=pid in AMBIENT,commands=['加入AI','AI退出','AI状态','AI重试'])
