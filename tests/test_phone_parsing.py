import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')  # 让 import 能找到项目根

from utils import normalize_phone
from data_helpers import split_phones, split_shareholders, _split_recommended


class TestNormalizePhone:
    def test_mobile_with_86_prefix(self):
        assert normalize_phone('+86 13800138000') == '13800138000'
        assert normalize_phone('8613800138000') == '13800138000'

    def test_nanchong_landline(self):
        assert normalize_phone('0817-3350888') == '3350888'
        assert normalize_phone('08173350888') == '3350888'

    def test_extension_preserved(self):
        assert normalize_phone('3350888-356') == '3350888-356'
        assert normalize_phone('0817-3350888-356') == '3350888-356'

    def test_other_area_code_kept(self):
        assert normalize_phone('0571-88889999') == '057188889999'
        assert normalize_phone('010-12345678') == '01012345678'

    def test_separator_rejected(self):
        assert normalize_phone('13800138000;13900139000') == ''
        assert normalize_phone('13800138000,13900139000') == ''
        assert normalize_phone('13800138000，13900139000') == ''

    def test_too_long_rejected(self):
        # 16 位 - 超过手机+分机极限
        assert normalize_phone('1380013800012345') == ''

    def test_empty(self):
        assert normalize_phone('') == ''
        assert normalize_phone(None) == ''


class TestSplitPhones:
    def test_main_phone_with_separator(self):
        # 关键测试：主号字段含分号也能拆分
        result = split_phones('13800138000;13900139000', '')
        assert len(result) == 2
        assert result[0][1] == '13800138000'
        assert result[1][1] == '13900139000'

    def test_other_phone_with_separator(self):
        result = split_phones('', '13800138000;13900139000')
        assert len(result) == 2

    def test_all_separator_types(self):
        for sep in [';', '；', ',', '，', '、', '/']:
            result = split_phones(f'13800138000{sep}13900139000', '')
            assert len(result) == 2, f'分隔符 {sep!r} 没拆开'

    def test_dedup_by_normalized(self):
        # 同一个号重复出现，去重
        result = split_phones('13800138000;13800138000', '')
        assert len(result) == 1

    def test_main_and_other_combined(self):
        result = split_phones('13800138000', '13900139000;13700137000')
        assert len(result) == 3
        assert result[0][1] == '13800138000'  # 主号排第一

    def test_invalid_phones_filtered(self):
        # 含分隔符 -> normalize 返回空 -> 被过滤
        # 但因为这里 split_phones 已经拆了，所以正常号会保留
        result = split_phones('13800138000;invalid', '')
        assert any(r[1] == '13800138000' for r in result)

    def test_empty_input(self):
        assert split_phones('', '') == []
        assert split_phones(None, None) == []


class TestSplitShareholders:
    def test_chinese_comma(self):
        result = split_shareholders('张三,李四')
        assert len(result) == 2

    def test_chinese_full_comma(self):
        result = split_shareholders('张三，李四')
        assert len(result) == 2

    def test_chinese_dunhao(self):
        # 顿号 - 股东最常见分隔符
        result = split_shareholders('张三、李四、王五')
        assert len(result) == 3

    def test_mixed_separators(self):
        result = split_shareholders('张三、李四;王五，赵六')
        assert len(result) == 4

    def test_dash_filtered(self):
        result = split_shareholders('张三;-')
        assert len(result) == 1


class TestSplitRecommended:
    def test_separators(self):
        for sep in [';', '；', ',', '，', '、', '/']:
            result = _split_recommended(f'13800138000{sep}13900139000')
            assert len(result) == 2, f'分隔符 {sep!r} 没拆开'

    def test_empty(self):
        assert _split_recommended('') == []
        assert _split_recommended(None) == []
