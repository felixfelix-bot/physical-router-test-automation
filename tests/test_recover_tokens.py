"""Tests for recover-tokens.py token recovery tool."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add scripts dir to path — absolute path for reliability
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class TestParseTokenFile:
    """Test parsing of tokens-to-recover.txt format."""

    def test_parse_single_line(self, tmp_path):
        """Parse a single valid token line."""
        from recover_tokens import parse_token_file

        f = tmp_path / "tokens.txt"
        f.write_text(
            "2026-06-18T16:14:05Z | https://testnut.cashu.exchange | cashuBxyz123 | failed to open gate: exit status 1\n"
        )
        records = parse_token_file(str(f))
        assert len(records) == 1
        assert records[0]["timestamp"] == "2026-06-18T16:14:05Z"
        assert records[0]["mint_url"] == "https://testnut.cashu.exchange"
        assert records[0]["token"] == "cashuBxyz123"
        assert records[0]["error"] == "failed to open gate: exit status 1"

    def test_parse_multiple_lines(self, tmp_path):
        """Parse multiple token lines."""
        from recover_tokens import parse_token_file

        f = tmp_path / "tokens.txt"
        f.write_text(
            "2026-06-18T16:14:05Z | https://testnut.cashu.exchange | cashuBtoken1 | error1\n"
            "2026-06-19T10:00:00Z | https://nofee.testnut.cashu.space | cashuBtoken2 | error2\n"
        )
        records = parse_token_file(str(f))
        assert len(records) == 2
        assert records[1]["mint_url"] == "https://nofee.testnut.cashu.space"

    def test_parse_empty_file(self, tmp_path):
        """Empty file returns empty list."""
        from recover_tokens import parse_token_file

        f = tmp_path / "tokens.txt"
        f.write_text("")
        records = parse_token_file(str(f))
        assert records == []

    def test_parse_line_without_error(self, tmp_path):
        """Line with only 3 fields (no error) still parses."""
        from recover_tokens import parse_token_file

        f = tmp_path / "tokens.txt"
        f.write_text("2026-06-18T16:14:05Z | https://testnut.cashu.exchange | cashuBtoken\n")
        records = parse_token_file(str(f))
        assert len(records) == 1
        assert records[0]["error"] == ""

    def test_parse_line_too_short(self, tmp_path):
        """Line with fewer than 3 fields is skipped."""
        from recover_tokens import parse_token_file

        f = tmp_path / "tokens.txt"
        f.write_text("incomplete | line\n")
        records = parse_token_file(str(f))
        assert records == []


class TestExtractProofs:
    """Test proof extraction from decoded tokens."""

    def test_extract_v3_format(self):
        """Extract proofs from V3 token structure."""
        from recover_tokens import extract_proofs

        decoded = {
            "token": [
                {"mint": "https://testnut.cashu.exchange", "proofs": [{"amount": 1}, {"amount": 4}]}
            ]
        }
        proofs = extract_proofs(decoded)
        assert len(proofs) == 2

    def test_extract_v4_format(self):
        """Extract proofs from V4 nested token."""
        from recover_tokens import extract_proofs

        decoded = {"token": {"mint": "https://testnut.cashu.exchange", "proofs": [{"amount": 5}]}}
        proofs = extract_proofs(decoded)
        assert len(proofs) == 1


class TestGetTokenAmount:
    """Test amount calculation from proofs."""

    def test_sum_amounts(self):
        """Sum all proof amounts."""
        from recover_tokens import get_token_amount

        decoded = {"token": [{"proofs": [{"amount": 1}, {"amount": 4}, {"amount": 8}]}]}
        assert get_token_amount(decoded) == 13

    def test_empty_proofs(self):
        """Zero amount for no proofs."""
        from recover_tokens import get_token_amount

        decoded = {"token": [{"proofs": []}]}
        assert get_token_amount(decoded) == 0


class TestCheckTokenState:
    """Test checkstate logic with mocked HTTP."""

    @patch("recover_tokens.requests.post")
    @patch("recover_tokens.decode_cashu_token")
    def test_check_unspent(self, mock_decode, mock_post):
        """All proofs unspent."""
        from recover_tokens import check_token_state

        mock_decode.return_value = {"token": [{"proofs": [{"Y": "abc", "amount": 1}]}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"states": [{"state": "UNSPENT"}, {"state": "UNSPENT"}]}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = check_token_state("https://testnut.cashu.exchange", "cashuBfake")
        assert result["unspent_count"] == 2
        assert result["spent_count"] == 0

    @patch("recover_tokens.requests.post")
    @patch("recover_tokens.decode_cashu_token")
    def test_check_mixed(self, mock_decode, mock_post):
        """Some spent, some unspent."""
        from recover_tokens import check_token_state

        mock_decode.return_value = {"token": [{"proofs": [{"Y": "abc", "amount": 1}]}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "states": [{"state": "UNSPENT"}, {"state": "SPENT"}]
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = check_token_state("https://testnut.cashu.exchange", "cashuBfake")
        assert result["unspent_count"] == 1
        assert result["spent_count"] == 1


class TestSubmitTokenToRouter:
    """Test token submission to router API."""

    @patch("recover_tokens.requests.post")
    def test_submit_success(self, mock_post):
        """Token accepted, HTTP 200."""
        from recover_tokens import submit_token_to_router

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"ok": true}'
        mock_post.return_value = mock_resp

        status, body = submit_token_to_router("192.168.8.1", "cashuBtoken")
        assert status == 200

    @patch("recover_tokens.requests.post")
    def test_submit_nds_error(self, mock_post):
        """Token consumed but NDS gate-open failed (HTTP 400)."""
        from recover_tokens import submit_token_to_router

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"content": "failed to open gate: exit status 1"}'
        mock_post.return_value = mock_resp

        status, body = submit_token_to_router("192.168.8.1", "cashuBtoken")
        assert status == 400
        assert "failed to open gate" in body
