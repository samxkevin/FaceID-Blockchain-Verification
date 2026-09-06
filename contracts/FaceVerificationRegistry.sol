// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title FaceVerificationRegistry
/// @notice Append-only registry of tamper-evident commitments to off-chain
///         face-verification evidence records.
///
/// @dev DESIGN NOTES (kept deliberately small and auditable)
///
///  What is stored on-chain:
///    - recordHash : SHA-256 of the canonical JSON evidence subtree
///    - urlHash    : keccak256 of the matched URL (compact commitment)
///    - blockTime  : the block timestamp at registration
///    - submitter  : the address that paid for the transaction
///
///  What is intentionally NOT stored on-chain:
///    - Face embeddings or any other biometric data. Biometrics are permanent
///      and irrevocable; publishing them to an immutable public ledger would be
///      irresponsible and is never done by this project.
///    - The image itself, or any personal data.
///    - The matched URL as a string. v1 stored the full URL, which is unbounded
///      calldata and storage cost that grows with URL length. Storing
///      keccak256(url) is a fixed 32 bytes and is equally tamper-evident: a
///      verifier who holds the record can recompute the hash and compare, but
///      cannot enumerate URLs from the chain alone. The full URL remains in the
///      off-chain evidence record, which the recordHash already commits to.
///
///  Immutability: there is no owner, no upgrade path, no delete and no update
///  function. Once written, an entry can never be altered, which is what makes
///  the record tamper-evident.
contract FaceVerificationRegistry {
    struct Verification {
        bytes32 recordHash; // SHA-256 of the canonical evidence subtree
        bytes32 urlHash; // keccak256 of the matched URL string
        uint64 blockTime; // block.timestamp (uint64 lasts beyond year 500,000)
        address submitter; // who registered it
    }

    /// @dev recordHash => entry. Packs into two storage slots:
    ///      slot0 recordHash, slot1 urlHash, slot2 (blockTime|submitter).
    mapping(bytes32 => Verification) private _records;

    /// @notice Total number of registrations, for cheap enumeration/sanity checks.
    uint256 public totalRecords;

    /// @notice Emitted once per successful registration. Indexed fields let a
    ///         verifier locate the anchoring transaction from the record hash
    ///         alone, with no archive-node log scanning by address.
    event VerificationRegistered(
        bytes32 indexed recordHash,
        bytes32 indexed urlHash,
        address indexed submitter,
        uint64 blockTime,
        uint256 recordIndex
    );

    error EmptyRecordHash();
    error EmptyUrlHash();
    error RecordAlreadyExists(bytes32 recordHash, uint64 existingBlockTime);
    error RecordNotFound(bytes32 recordHash);

    /// @notice Register a commitment to an off-chain evidence record.
    /// @param recordHash SHA-256 of the canonical evidence JSON.
    /// @param urlHash    keccak256 of the matched social-media URL.
    /// @dev Duplicate recordHash values are rejected so the first anchoring of
    ///      a given evidence record is the authoritative one and its timestamp
    ///      can never be back-dated or overwritten.
    function registerVerification(bytes32 recordHash, bytes32 urlHash) external {
        if (recordHash == bytes32(0)) revert EmptyRecordHash();
        if (urlHash == bytes32(0)) revert EmptyUrlHash();

        Verification storage existing = _records[recordHash];
        if (existing.blockTime != 0) {
            revert RecordAlreadyExists(recordHash, existing.blockTime);
        }

        uint64 nowTime = uint64(block.timestamp);
        _records[recordHash] = Verification({
            recordHash: recordHash,
            urlHash: urlHash,
            blockTime: nowTime,
            submitter: msg.sender
        });

        uint256 index = totalRecords;
        unchecked {
            totalRecords = index + 1;
        }

        emit VerificationRegistered(recordHash, urlHash, msg.sender, nowTime, index);
    }

    /// @notice Read a registration. Reverts when the record was never anchored,
    ///         so a caller can never mistake "absent" for "zero-valued".
    function getVerification(bytes32 recordHash)
        external
        view
        returns (bytes32, bytes32, uint64, address)
    {
        Verification memory entry = _records[recordHash];
        if (entry.blockTime == 0) revert RecordNotFound(recordHash);
        return (entry.recordHash, entry.urlHash, entry.blockTime, entry.submitter);
    }

    /// @notice Non-reverting existence check, convenient for verifier scripts.
    function exists(bytes32 recordHash) external view returns (bool) {
        return _records[recordHash].blockTime != 0;
    }

    /// @notice One-call verification helper.
    /// @dev Lets an independent verifier confirm, in a single eth_call, that a
    ///      record hash is anchored AND that it was anchored against the exact
    ///      URL they hold locally.
    function verify(bytes32 recordHash, bytes32 urlHash)
        external
        view
        returns (bool anchored, bool urlMatches, uint64 blockTime, address submitter)
    {
        Verification memory entry = _records[recordHash];
        anchored = entry.blockTime != 0;
        urlMatches = anchored && entry.urlHash == urlHash;
        blockTime = entry.blockTime;
        submitter = entry.submitter;
    }
}
