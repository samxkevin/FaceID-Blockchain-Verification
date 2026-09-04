// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract FaceVerificationRegistry {
    struct Verification {
        bytes32 recordHash;
        string matchedUrl;
        uint256 timestamp;
        address submitter;
    }

    mapping(bytes32 => Verification) public records;

    event VerificationRegistered(
        bytes32 indexed recordHash,
        string matchedUrl,
        uint256 timestamp,
        address indexed submitter
    );

    function registerVerification(bytes32 recordHash, string calldata matchedUrl) external {
        require(recordHash != bytes32(0), "empty hash");
        require(bytes(matchedUrl).length > 0, "empty URL");
        require(records[recordHash].timestamp == 0, "record already exists");

        records[recordHash] = Verification({
            recordHash: recordHash,
            matchedUrl: matchedUrl,
            timestamp: block.timestamp,
            submitter: msg.sender
        });

        emit VerificationRegistered(recordHash, matchedUrl, block.timestamp, msg.sender);
    }

    function getVerification(bytes32 recordHash)
        external
        view
        returns (bytes32, string memory, uint256, address)
    {
        Verification memory v = records[recordHash];
        return (v.recordHash, v.matchedUrl, v.timestamp, v.submitter);
    }
}
