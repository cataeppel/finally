import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatPanel from "@/components/ChatPanel";
import { getChatHistory, sendChatMessage } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

jest.mock("@/lib/api", () => ({
  getChatHistory: jest.fn(),
  sendChatMessage: jest.fn(),
}));

const mockHistory = getChatHistory as jest.MockedFunction<typeof getChatHistory>;
const mockSend = sendChatMessage as jest.MockedFunction<typeof sendChatMessage>;

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "Here is your portfolio.",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockHistory.mockReset().mockResolvedValue([]);
  mockSend.mockReset();
});

describe("ChatPanel", () => {
  it("shows suggestions until a message exists", async () => {
    render(<ChatPanel onTradeExecuted={jest.fn()} />);
    expect(await screen.findByText("How is my portfolio doing?")).toBeInTheDocument();
  });

  it("restores persisted conversation history on mount", async () => {
    mockHistory.mockResolvedValue([
      {
        id: "h1",
        role: "user",
        content: "hello there",
        created_at: "2026-01-01T00:00:00Z",
      },
      assistant({ id: "h2", content: "I am FinAlly." }),
    ]);

    render(<ChatPanel onTradeExecuted={jest.fn()} />);

    expect(await screen.findByText("hello there")).toBeInTheDocument();
    expect(screen.getByText("I am FinAlly.")).toBeInTheDocument();
  });

  it("renders the user message and the assistant reply", async () => {
    const user = userEvent.setup();
    mockSend.mockResolvedValue({ message: assistant() });
    render(<ChatPanel onTradeExecuted={jest.fn()} />);

    await user.type(screen.getByTestId("chat-input"), "show my portfolio");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByText("show my portfolio")).toBeInTheDocument();
    expect(await screen.findByText("Here is your portfolio.")).toBeInTheDocument();
    await waitFor(() => expect(mockSend).toHaveBeenCalledWith("show my portfolio"));
  });

  it("shows a loading indicator while awaiting the LLM", async () => {
    const user = userEvent.setup();
    let resolve!: (v: { message: ChatMessage }) => void;
    mockSend.mockReturnValue(new Promise((r) => (resolve = r)));

    render(<ChatPanel onTradeExecuted={jest.fn()} />);
    await user.type(screen.getByTestId("chat-input"), "hi");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByTestId("chat-loading")).toBeInTheDocument();

    resolve({ message: assistant({ content: "Hello." }) });
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
  });

  it("renders executed trades inline and refreshes the portfolio", async () => {
    const user = userEvent.setup();
    const onTradeExecuted = jest.fn();
    mockSend.mockResolvedValue({
      message: assistant({
        content: "Bought AAPL.",
        actions: {
          trades: [
            {
              id: "t1",
              ticker: "AAPL",
              side: "buy",
              quantity: 10,
              price: 190.5,
              executed_at: "2026-01-01T00:00:00Z",
            },
          ],
        },
      }),
    });

    render(<ChatPanel onTradeExecuted={onTradeExecuted} />);
    await user.type(screen.getByTestId("chat-input"), "buy 10 AAPL");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByText(/BUY 10 AAPL @ 190\.50/)).toBeInTheDocument();
    await waitFor(() => expect(onTradeExecuted).toHaveBeenCalled());
  });

  it("renders watchlist changes and trade errors inline", async () => {
    const user = userEvent.setup();
    mockSend.mockResolvedValue({
      message: assistant({
        content: "Done.",
        actions: {
          watchlist_changes: [{ ticker: "PYPL", action: "add" }],
          errors: ["Insufficient cash. Need $50,000.00"],
        },
      }),
    });

    render(<ChatPanel onTradeExecuted={jest.fn()} />);
    await user.type(screen.getByTestId("chat-input"), "add PYPL");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByText(/Watchlist \+ PYPL/)).toBeInTheDocument();
    expect(screen.getByText(/Insufficient cash/)).toBeInTheDocument();
  });

  it("shows the error as an assistant message when the request fails", async () => {
    const user = userEvent.setup();
    mockSend.mockRejectedValue(new Error("LLM unavailable"));

    render(<ChatPanel onTradeExecuted={jest.fn()} />);
    await user.type(screen.getByTestId("chat-input"), "hi");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByText("LLM unavailable")).toBeInTheDocument();
  });

  it("does not send an empty message", async () => {
    const user = userEvent.setup();
    render(<ChatPanel onTradeExecuted={jest.fn()} />);

    await user.type(screen.getByTestId("chat-input"), "   ");
    await user.keyboard("{Enter}");

    expect(mockSend).not.toHaveBeenCalled();
  });
});
