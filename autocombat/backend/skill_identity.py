"""Persistent skill identities and command slots, independent of list order."""
import copy
import re
import uuid

MAX_SKILL_INDEX = 30
IDENTITY_FIELDS = ('skill_id', 'skill_index')


def identify_skills(character, previous=None):
    """Number legacy skills before filtering; preserve IDs and never reuse slots."""
    result = copy.deepcopy(character)
    spells = result.get('spells', [])
    if not isinstance(spells, list) or any(not isinstance(s, dict) for s in spells):
        raise ValueError('技能必须为字典列表')
    old = (previous or {}).get('spells', [])
    by_id = {s['skill_id']: s for s in old if s.get('skill_id')}
    by_slot = {s['skill_index']: s for s in old if s.get('skill_index')}
    old_next = (previous or {}).get('next_skill_index', 1)
    next_index = result.get('next_skill_index', 1)
    if type(next_index) is not int or not 1 <= next_index <= MAX_SKILL_INDEX + 1:
        raise ValueError('技能编号分配记录无效')
    next_index = max(next_index, old_next, max((s.get('skill_index', 0) for s in old), default=0) + 1)
    ids, slots = set(), set()
    for spell in spells:
        sid, slot = spell.get('skill_id'), spell.get('skill_index')
        if sid is None and slot is None:
            # Old clients may omit metadata; recover only an unambiguous match.
            matches = [s for s in old if (s.get('name'), s.get('phase', 0), s.get('weapon_required')) ==
                       (spell.get('name'), spell.get('phase', 0), spell.get('weapon_required'))]
            if len(matches) == 1:
                sid, slot = (matches[0][key] for key in IDENTITY_FIELDS)
                spell.update(skill_id=sid, skill_index=slot)
            else:
                continue
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}', sid):
            raise ValueError('技能唯一编号格式无效')
        if type(slot) is not int or not 1 <= slot <= MAX_SKILL_INDEX:
            raise ValueError(f'技能固定编号必须为 1 至 {MAX_SKILL_INDEX} 的整数')
        if sid in ids or slot in slots:
            raise ValueError('技能唯一编号或固定编号重复（包括禁用技能）')
        if sid in by_id and by_id[sid]['skill_index'] != slot:
            raise ValueError('已保存技能的固定编号不能修改')
        if slot in by_slot and by_slot[slot]['skill_id'] != sid:
            raise ValueError('已保存技能的唯一编号不能修改')
        if previous is not None and sid not in by_id and slot < old_next:
            raise ValueError('新技能不能占用已经分配过的固定编号')
        ids.add(sid)
        slots.add(slot)
        next_index = max(next_index, slot + 1)
    for spell in spells:
        if spell.get('skill_id'):
            continue
        if next_index > MAX_SKILL_INDEX:
            raise ValueError(f'技能固定编号已用满（最多 {MAX_SKILL_INDEX} 个，禁用及删除不回收编号）')
        slot = next_index
        sid = (f'{result.get("serial", "CUSTOM")}-S{slot:03}' if previous is None
               else 'skill-' + uuid.uuid4().hex)
        if sid in ids:
            raise ValueError('技能唯一编号重复')
        spell.update(skill_id=sid, skill_index=slot)
        ids.add(sid)
        slots.add(slot)
        next_index += 1
    result['next_skill_index'] = next_index
    return result


def validate_global_skill_ids(characters):
    seen = set()
    for character in characters:
        for spell in character.get('spells', []):
            sid = spell['skill_id']
            if sid in seen:
                raise ValueError('不同角色的技能唯一编号重复：' + sid)
            seen.add(sid)
