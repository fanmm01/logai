#!/usr/bin/env python3
"""
Q-Learning AI自训练系统 (ai_trainer_pvp.py) — PvP版本
==================================================
使用 characters_data_pvp 数据集，输出 ai_weights_pvp.json

本模块是 ai_trainer.py 的轻量包装器：
  - 导入主训练器的全部逻辑
  - 仅覆盖数据源（characters_data_pvp）和输出文件（ai_weights_pvp.json）
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 覆盖数据源
import characters_data_pvp as _data
import battle_engine
battle_engine._SUMMON_TEMPLATES = _data.SUMMON_TEMPLATES

# 先设置输出文件名，再导入主训练器
import ai_trainer
ai_trainer.OUTPUT_FILE = 'ai_weights_pvp.json'

from ai_trainer import *

# 覆盖数据
ALL_CHARACTERS = _data.ALL_CHARACTERS
load_character_to_engine = _data.load_character_to_engine
SUMMON_TEMPLATES = _data.SUMMON_TEMPLATES

if __name__ == '__main__':
    main()
