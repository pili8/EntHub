import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')  # 让 import 能找到项目根

from utils import normalize_phone, validate_phone
from data_helpers import split_phones, split_emails, split_shareholders, _split_recommended


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
        result = split_phones('13800138000;13900139000')
        assert len(result) == 2
        assert result[0][1] == '13800138000'
        assert result[1][1] == '13900139000'

    def test_all_separator_types(self):
        for sep in [';', '；', ',', '，', '、', '/']:
            result = split_phones(f'13800138000{sep}13900139000')
            assert len(result) == 2, f'分隔符 {sep!r} 没拆开'

    def test_dedup_by_normalized(self):
        # 同一个号重复出现，去重
        result = split_phones('13800138000;13800138000')
        assert len(result) == 1

    def test_invalid_phones_filtered(self):
        # 无效号码（归一化后长度不对）会被校验跳过
        result = split_phones('13800138000;188703;12345')
        assert len(result) == 1  # 只有 13800138000 有效
        result = split_phones('13800138000;invalid')
        assert any(r[1] == '13800138000' for r in result)

    def test_empty_input(self):
        assert split_phones('') == []
        assert split_phones(None) == []


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


class TestValidatePhone:
    def test_mobile_valid(self):
        for prefix in '3456789':
            n = f'1{prefix}80013800'
            is_valid, ptype, _ = validate_phone(n)
            assert is_valid, f'{n} should be valid'
            assert ptype == 'mobile'

    def test_landline_7_digit(self):
        is_valid, ptype, _ = validate_phone('3350888')
        assert is_valid
        assert ptype == 'landline'

    def test_landline_with_area_code(self):
        is_valid, ptype, _ = validate_phone('057188889999')
        assert is_valid
        assert ptype == 'landline'

    def test_toll_free_400(self):
        is_valid, ptype, _ = validate_phone('4001234567')
        assert is_valid
        assert ptype == 'toll_free'

    def test_toll_free_800(self):
        is_valid, ptype, _ = validate_phone('8001234567')
        assert is_valid
        assert ptype == 'toll_free'

    def test_extension(self):
        is_valid, ptype, _ = validate_phone('3350888-356')
        assert is_valid
        assert ptype == 'landline_ext'

    def test_invalid_too_short(self):
        is_valid, _, _ = validate_phone('123')
        assert not is_valid

    def test_invalid_9_digit(self):
        is_valid, _, _ = validate_phone('123456789')
        assert not is_valid

    def test_invalid_11_non_mobile(self):
        # 11 位但不是手机号段（第二位 0/1/2）
        is_valid, _, _ = validate_phone('10000138000')
        assert not is_valid

    def test_empty(self):
        is_valid, _, _ = validate_phone('')
        assert not is_valid


class TestSplitEmails:
    def test_single_email(self):
        result = split_emails('a@b.com')
        assert len(result) == 1
        assert result[0] == ('a@b.com', 'a@b.com')

    def test_multiple_with_semicolon(self):
        result = split_emails('a@b.com;c@d.com')
        assert len(result) == 2

    def test_all_separator_types(self):
        for sep in [';', '；', ',', '，', '、', '/']:
            result = split_emails(f'a@b.com{sep}c@d.com')
            assert len(result) == 2, f'分隔符 {sep!r} 没拆开'

    def test_dedup(self):
        result = split_emails('a@b.com;A@B.COM;a@b.com')
        assert len(result) == 1  # lowercase 去重

    def test_empty(self):
        assert split_emails('') == []
        assert split_emails(None) == []

    def test_first_is_primary(self):
        result = split_emails('first@b.com;second@b.com')
        assert result[0][0] == 'first@b.com'
